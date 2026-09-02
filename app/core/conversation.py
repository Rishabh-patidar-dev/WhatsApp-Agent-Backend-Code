"""The conversation itself: menus, questions and the sign-up flow.

Two ways in, one brain. A tap arrives as a menu id and is answered from the
database; typed text goes through retrieval and the model. Both end up in the
same lead record, so a person can tap a course, ask a question about it, get
signed up, and it is one continuous conversation.

The sign-up flow is interruptible by design: a question asked halfway through
is answered on the spot and the flow resumes at the same field, because a lead
who asks "does this include the manual?" while typing their email is not a lead
you want to drop.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict, deque

from app.channels.whatsapp import client as wa
from app.channels.whatsapp.parse_webhook import IncomingMessage
from app.config import settings
from app.core import generation, menus, retrieval
from app.core.copy import t
from app.db import catalog, leads
from app.integrations import dashboard

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
MENU_WORDS = {"menu", "menú", "m", "0", "opciones", "options", "inicio", "start"}
EXIT_WORDS = {"e", "salir", "exit", "adios", "adiós", "bye", "gracias", "thanks"}
GREETING_WORDS = {"hi", "hello", "hey", "hola", "buenas", "buenos dias", "buenos días",
                  "buenas tardes", "buenas noches", "que tal", "qué tal"}
ENROLL_WORDS = ("enroll", "enrol", "sign up", "signup", "register",
                "inscrib", "registrar", "apuntar", "anotar")
CANCEL_WORDS = ("cancel", "cancelar", "stop", "nevermind", "never mind", "detener")
QUESTION_STARTERS = (
    "what", "how", "when", "where", "why", "which", "who", "can ", "could ", "do you",
    "does ", "is there", "are there", "will ", "should ", "would ", "tell me", "explain",
    "qué", "que ", "cómo", "como ", "cuándo", "cuando ", "dónde", "donde ", "por qué",
    "porque ", "cuál", "cual ", "cuánto", "cuanto ", "quién", "quien ", "hay ", "tienen",
    "puedo", "puede", "me interesa", "información", "informacion",
)

# --- per-phone serialisation and rate limiting ------------------------------
_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_locks_guard = threading.Lock()
_recent: dict[str, deque] = defaultdict(deque)
_recent_guard = threading.Lock()


def _lock_for(phone: str) -> threading.Lock:
    with _locks_guard:
        return _locks[phone]


def _rate_limited(phone: str) -> bool:
    """Caps how fast one number can drive the model. Protects cost and the API."""
    now = time.monotonic()
    window = settings.rate_limit_window_seconds
    with _recent_guard:
        stamps = _recent[phone]
        while stamps and now - stamps[0] > window:
            stamps.popleft()
        if len(stamps) >= settings.rate_limit_messages:
            return True
        stamps.append(now)
        return False


def is_question(text: str) -> bool:
    lowered = text.lower().strip()
    return "?" in lowered or lowered.startswith(QUESTION_STARTERS)


def to_local_phone(value: str) -> str:
    """Mirrors the dashboard's own toLocalPhone(), so a number accepted here
    always passes its validation."""
    digits = "".join(c for c in value if c.isdigit())
    if len(digits) == 12 and digits.startswith("52"):
        return digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        return digits[1:]
    return digits


# --- entry point ------------------------------------------------------------
def handle(message: IncomingMessage) -> None:
    """Runs in a background task, one message at a time per phone number."""
    with _lock_for(message.from_phone):
        language = "es"
        try:
            language = _handle(message)
        except Exception as exc:
            log.exception("Message handling failed for %s: %r", _mask(message.from_phone), exc)
            wa.send_text(message.from_phone, t("error_retry", language))


def _mask(phone: str) -> str:
    return f"…{phone[-4:]}" if len(phone) > 4 else "…"


def _handle(message: IncomingMessage) -> str:
    phone = message.from_phone
    lead, is_new = leads.get_or_create(phone)
    language = lead.get("language") or "es"

    if lead.get("human_takeover"):
        log.info("Lead %s is under human takeover — bot staying silent", _mask(phone))
        return language

    if _rate_limited(phone):
        log.warning("Rate limit hit for %s", _mask(phone))
        wa.send_text(phone, t("rate_limited", language))
        return language

    text = (message.text or "").strip()[: settings.max_user_chars]
    lowered = text.lower()

    if message.contact_name and not lead.get("name"):
        leads.set_name(phone, message.contact_name)

    if is_new:
        _send_greeting(phone, language, message.contact_name, new=True)
        leads.set_state(phone, leads.CHATTING)
        lead["state"] = leads.CHATTING
        if not text or lowered in GREETING_WORDS or message.is_tap:
            return language

    # A tap carries unambiguous intent, so it is handled before anything else.
    if message.is_tap:
        return _handle_tap(lead, message, language)

    if not text:
        wa.send_text(phone, t("unsupported_media", language))
        return language

    if lowered in MENU_WORDS:
        _send_main_menu(phone, language)
        return language

    if lowered in GREETING_WORDS:
        _send_greeting(phone, language, lead.get("name"), new=False)
        return language

    if lowered in EXIT_WORDS:
        wa.send_text(phone, t("chat_hint", language))
        return language

    state = lead.get("state") or leads.CHATTING

    if state in leads.ENROLL_STATES:
        return _handle_enrollment(lead, text, lowered, language)

    if any(word in lowered for word in ENROLL_WORDS):
        _start_enrollment(phone, language)
        return language

    # If they are looking at a course card, a follow-up like "how long is it?"
    # is about that course.
    menu_state = leads.as_json(lead.get("menu_state"))
    focus = menu_state.get("course_id") if menu_state.get("screen") == "course" else None

    _answer_question(lead, text, language, with_hint=True, focus_course_id=focus)
    return language


# --- greetings and menus ----------------------------------------------------
def _send_greeting(phone: str, language: str, name: str | None, new: bool) -> None:
    suffix = f" {name.split()[0]}" if name else ""
    if new:
        body = t("greeting_new", language, name=suffix, total=catalog.total_courses())
    else:
        body = t("greeting_back", language, name=suffix)
    menu = menus.main_menu(language)
    wa.send_list(phone, body, menu["button_label"], menu["sections"],
                 header=menu["header"], footer=menu["footer"])


def _send_main_menu(phone: str, language: str) -> None:
    menu = menus.main_menu(language)
    wa.send_list(phone, menu["body"], menu["button_label"], menu["sections"],
                 header=menu["header"], footer=menu["footer"])
    leads.set_menu_state(phone, {"screen": "main"})


def _send_browse_menu(phone: str, language: str) -> None:
    menu = menus.browse_menu(language)
    wa.send_list(phone, menu["body"], menu["button_label"], menu["sections"],
                 header=menu["header"], footer=menu["footer"])
    leads.set_menu_state(phone, {"screen": "browse"})


def _send_course_list(phone: str, language: str, group: str, offset: int) -> None:
    menu = menus.course_list(group, language, offset)
    if not menu:
        wa.send_text(phone, t("no_courses_in_group", language))
        return
    wa.send_list(phone, menu["body"], menu["button_label"], menu["sections"],
                 header=menu["header"], footer=menu["footer"])
    leads.set_menu_state(phone, {"screen": "group", "group": group, "offset": offset})


def _send_course_detail(phone: str, language: str, course_id: str) -> None:
    course = catalog.get_course(course_id)
    if not course:
        _send_browse_menu(phone, language)
        return
    wa.send_text(phone, menus.course_detail(course, language))
    wa.send_buttons(phone, t("course_actions", language), menus.course_buttons(course_id, language))
    leads.set_menu_state(phone, {"screen": "course", "course_id": course_id})


# --- taps -------------------------------------------------------------------
def _handle_tap(lead: dict, message: IncomingMessage, language: str) -> str:
    phone = lead["phone"]
    action = menus.parse_action(message.reply_id)

    if action.kind == "menu":
        if action.value == "browse":
            _send_browse_menu(phone, language)
        else:
            _send_main_menu(phone, language)
        return language

    if action.kind == "group":
        _send_course_list(phone, language, action.value, action.offset)
        return language

    if action.kind == "course":
        _send_course_detail(phone, language, action.course_id)
        return language

    if action.kind == "unsupported":
        wa.send_text(phone, t("unsupported_media", language))
        return language

    if action.kind == "act":
        return _handle_act(lead, action, language)

    _send_main_menu(phone, language)
    return language


def _handle_act(lead: dict, action: menus.Action, language: str) -> str:
    phone = lead["phone"]

    if action.value == "enroll":
        _start_enrollment(phone, language, course_id=action.course_id or None)
    elif action.value == "prices":
        wa.send_text(phone, t("prices_info", language))
    elif action.value == "requirements":
        wa.send_text(phone, t("requirements_info", language))
    elif action.value == "faq":
        wa.send_text(phone, t("faq_info", language))
    elif action.value == "contact":
        wa.send_text(phone, t("contact_info", language))
    elif action.value == "language":
        new_language = "en" if language == "es" else "es"
        leads.set_language(phone, new_language)
        wa.send_text(phone, t("language_switched", new_language))
        _send_main_menu(phone, new_language)
        return new_language
    else:
        _send_main_menu(phone, language)
    return language


# --- questions --------------------------------------------------------------
def _answer_question(
    lead: dict, text: str, language: str, with_hint: bool, focus_course_id: str | None = None
) -> str:
    phone = lead["phone"]
    result = generation.answer(text, lead.get("history") or [], language, focus_course_id)

    if not result.confident:
        retrieval.log_unanswered(phone, text, result.top_score, language)

    body = f"{result.text}\n\n{t('chat_hint', language)}" if with_hint else result.text
    wa.send_text(phone, body)
    leads.append_history(lead, text, result.text)
    return result.text


# --- sign-up flow -----------------------------------------------------------
def _start_enrollment(phone: str, language: str, course_id: str | None = None) -> None:
    course = catalog.get_course(course_id) if course_id else None
    if course:
        name = course["name_es"] if language == "es" else course["name_en"]
        leads.set_draft(phone, {"course": name, "course_id": course["course_id"]})
        leads.set_state(phone, leads.ENROLL_NAME)
        wa.send_text(phone, t("enroll_name", language, course=name))
    else:
        leads.set_draft(phone, {})
        leads.set_state(phone, leads.ENROLL_COURSE)
        wa.send_text(phone, t("enroll_course", language))


def _handle_enrollment(lead: dict, text: str, lowered: str, language: str) -> str:
    phone = lead["phone"]
    state = lead["state"]
    draft = leads.as_json(lead.get("enrollment_draft"))

    if any(word in lowered for word in CANCEL_WORDS):
        leads.set_state(phone, leads.CHATTING)
        leads.set_draft(phone, {})
        wa.send_text(phone, t("enroll_cancelled", language))
        return language

    # Question mid-flow: answer it, then re-ask the field we were on. State and
    # draft are untouched, so nothing collected so far is lost.
    if is_question(text):
        # The course being signed up for is the subject, even when the question
        # doesn't name it ("does it include the manual?").
        _answer_question(lead, text, language, with_hint=False,
                         focus_course_id=draft.get("course_id"))
        wa.send_text(phone, f"{t('resume_note', language)}\n{_current_prompt(state, language, draft)}")
        log.info("Answered mid-enrollment question for %s, resuming at %s", _mask(phone), state)
        return language

    if state == leads.ENROLL_COURSE:
        matches = catalog.search_by_name(text, limit=1)
        if matches:
            draft["course"] = matches[0]["name_es"] if language == "es" else matches[0]["name_en"]
            draft["course_id"] = matches[0]["course_id"]
        else:
            draft["course"] = text  # keep what they typed; the team can resolve it
        leads.set_draft(phone, draft)
        leads.set_state(phone, leads.ENROLL_NAME)
        wa.send_text(phone, t("enroll_name", language, course=draft["course"]))
        return language

    if state == leads.ENROLL_NAME:
        draft["name"] = text
        leads.set_draft(phone, draft)
        leads.set_name(phone, text)
        leads.set_state(phone, leads.ENROLL_EMAIL)
        wa.send_text(phone, t("enroll_email", language, name=text.split()[0]))
        return language

    if state == leads.ENROLL_EMAIL:
        if not EMAIL_RE.match(text):
            wa.send_text(phone, t("enroll_email_retry", language))
            return language
        draft["email"] = text
        leads.set_draft(phone, draft)
        leads.set_state(phone, leads.ENROLL_PHONE)
        wa.send_text(phone, t("enroll_phone", language))
        return language

    if state == leads.ENROLL_PHONE:
        if len(to_local_phone(text)) != 10:
            wa.send_text(phone, t("enroll_phone_retry", language))
            return language
        draft["phone"] = text
        leads.set_draft(phone, draft)
        leads.set_state(phone, leads.ENROLL_ADDRESS)
        wa.send_text(phone, t("enroll_address", language))
        return language

    if state == leads.ENROLL_ADDRESS:
        draft["address"] = text
        # State moves before the push so a duplicate delivery cannot double-submit.
        leads.set_state(phone, leads.CHATTING)
        leads.set_draft(phone, {})
        dashboard.push_lead(phone, draft, language)
        wa.send_text(phone, t(
            "enroll_complete", language,
            name=draft.get("name", "").split()[0] if draft.get("name") else "",
            course=draft.get("course", ""),
            email=draft.get("email", ""),
            phone=draft.get("phone", ""),
        ))
        _send_main_menu(phone, language)
        return language

    leads.set_state(phone, leads.CHATTING)
    return language


def _current_prompt(state: str, language: str, draft: dict) -> str:
    if state == leads.ENROLL_COURSE:
        return t("enroll_course", language)
    if state == leads.ENROLL_NAME:
        return t("enroll_name", language, course=draft.get("course", ""))
    if state == leads.ENROLL_EMAIL:
        first_name = (draft.get("name") or "").split()
        return t("enroll_email", language, name=first_name[0] if first_name else "")
    if state == leads.ENROLL_PHONE:
        return t("enroll_phone", language)
    return t("enroll_address", language)
