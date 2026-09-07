"""The conversation, run as five stages.

    greet → qualify → educate → propose → confirm

Greet is one line. Qualify then collects every detail up front — name, age,
email, phone, city — before any course talk begins. Age matters twice over: the
catalogue gates eligibility at 15 and 18, so it decides what can be recommended
at all. Only once the details are on file does the agent ask what the person
came for. Educate is the resting state: browsing, questions, retrieval. Propose
puts one specific course forward. Confirm needs nothing but the course, because
everything else was already taken, so enrolling is a single tap.

Two ways in, one brain: a tap arrives as a menu id and is answered from the
database, typed text goes through retrieval and the model. Both move the same
lead through the same stages.

Nothing here is a dead end. A question asked in the middle of any stage is
answered on the spot, and the stage resumes at the exact point it was left.
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
from app.core.copy import PROFILES, t
from app.db import catalog, leads
from app.integrations import dashboard

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
AGE_RE = re.compile(r"\b(\d{1,2})\b")
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

# Typed answers to "who is this for?", so the qualifying step doesn't force a tap.
PROFILE_HINTS = {
    "public": ("mí", "mi familia", "familia", "personal", "myself", "family", "me", "casa"),
    "employees": ("empresa", "trabajo", "brigada", "empleados", "company", "work", "team", "staff"),
    "health": ("salud", "enfermer", "medic", "médic", "paramédic", "paramedic", "tum",
               "health", "nurse", "doctor", "hospital"),
    "rescue": ("rescate", "bombero", "guardavidas", "rescue", "firefighter", "lifeguard"),
    "diploma": ("diplomado", "diploma", "carrera", "certificación larga"),
}

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


def parse_age(text: str) -> int | None:
    """A typed age. Anything outside 5–99 is treated as not-an-age."""
    found = AGE_RE.search(text)
    if not found:
        return None
    age = int(found.group(1))
    return age if 5 <= age <= 99 else None


def match_profile(text: str) -> str | None:
    lowered = text.lower()
    for profile, hints in PROFILE_HINTS.items():
        if any(hint in lowered for hint in hints):
            return profile
    return None


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

    # --- stage 1: greet, then straight into qualifying ---
    if is_new:
        _greet_and_qualify(lead, language, message.contact_name)
        return language

    if message.is_tap:
        return _handle_tap(lead, message, language)

    if not text:
        wa.send_text(phone, t("unsupported_media", language))
        return language

    state = leads.normalise_state(lead.get("state"))
    qualification = leads.as_json(lead.get("qualification"))

    # These work from any stage — except that the menu does not open until the
    # details are on file, so nobody skips past the questions.
    if lowered in MENU_WORDS:
        if leads.is_qualified(qualification):
            _send_main_menu(phone, language)
        else:
            _ask_next_detail(lead, language, qualification)
        return language

    if lowered in GREETING_WORDS and state not in leads.CONFIRM_STATES:
        if not leads.is_qualified(qualification):
            _greet_and_qualify(lead, language, lead.get("name"), returning=True)
        else:
            _send_greeting(phone, language, lead.get("name"), new=False)
        return language

    if lowered in EXIT_WORDS and state not in leads.CONFIRM_STATES:
        wa.send_text(phone, t("chat_hint", language))
        return language

    # --- stage 2: qualify ---
    if state in leads.QUALIFY_STATES:
        return _handle_qualify(lead, text, lowered, language, state, qualification)

    # --- stage 5: confirm ---
    if state in leads.CONFIRM_STATES:
        return _handle_confirm(lead, text, lowered, language, state)

    # An older lead, or one that dropped out mid-questions: finish them first.
    if not leads.is_qualified(qualification):
        _answer_question(lead, text, language, with_hint=False)
        return _ask_next_detail(lead, language, qualification)

    # --- stages 3 & 4: educate / propose ---
    if any(word in lowered for word in ENROLL_WORDS):
        _start_confirm(lead, language, course_id=_proposed_course_id(lead))
        return language

    focus = _focus_course_id(lead)
    _answer_question(lead, text, language, with_hint=True, focus_course_id=focus)
    return language


def _focus_course_id(lead: dict) -> str | None:
    """The course currently on screen, so follow-ups don't have to name it."""
    menu_state = leads.as_json(lead.get("menu_state"))
    if menu_state.get("screen") in ("course", "propose"):
        return menu_state.get("course_id")
    return None


def _proposed_course_id(lead: dict) -> str | None:
    return _focus_course_id(lead)


# --- stage 1: greet ---------------------------------------------------------
def _greet_and_qualify(lead: dict, language: str, name: str | None,
                       returning: bool = False) -> None:
    """Greeting is one short line, then straight into collecting the details."""
    phone = lead["phone"]
    suffix = f" {name.split()[0]}" if name else ""
    wa.send_text(phone, t("greeting_back" if returning else "greeting_new", language, name=suffix))
    wa.send_text(phone, t("qualify_intro", language))
    _ask_next_detail(lead, language, leads.as_json(lead.get("qualification")))


def _ask_next_detail(lead: dict, language: str, qualification: dict) -> str:
    """Asks for the first detail still missing; opens the menu once none are."""
    phone = lead["phone"]
    state = leads.next_qualify_state(qualification)

    if state is None:
        return _finish_qualifying(lead, language, qualification)

    leads.set_state(phone, state)
    leads.set_menu_state(phone, {"screen": state.lower()})

    if state == leads.QUALIFY_AGE:
        # Tappable bands, but a typed number is accepted and preferred.
        wa.send_buttons(phone, t("qualify_age", language), menus.age_buttons(language))
    else:
        wa.send_text(phone, t(leads.QUALIFY_PROMPTS[state], language))
    return language


def _finish_qualifying(lead: dict, language: str, qualification: dict) -> str:
    """Every detail is on file — now ask what they actually came for."""
    phone = lead["phone"]
    leads.set_state(phone, leads.EDUCATE)
    first_name = (qualification.get("name") or "").split()
    suffix = f" {first_name[0]}" if first_name else ""
    wa.send_text(phone, t("qualify_done", language, name=suffix))
    _send_main_menu(phone, language)
    return language


def _ask_profile(phone: str, language: str) -> None:
    menu = menus.profile_menu(language)
    wa.send_list(phone, menu["body"], menu["button_label"], menu["sections"],
                 header=menu["header"], footer=menu["footer"])
    leads.set_state(phone, leads.QUALIFY_PROFILE)
    leads.set_menu_state(phone, {"screen": "qualify_profile"})


def _send_greeting(phone: str, language: str, name: str | None, new: bool) -> None:
    suffix = f" {name.split()[0]}" if name else ""
    wa.send_text(phone, t("greeting_new" if new else "greeting_back", language, name=suffix))
    _send_main_menu(phone, language)


# --- stage 2: qualify -------------------------------------------------------
def _handle_qualify(lead: dict, text: str, lowered: str, language: str,
                    state: str, qualification: dict) -> str:
    """One detail per message, with the field validated before it is stored."""
    phone = lead["phone"]

    # A question instead of an answer: answer it, then ask the same thing again.
    if is_question(text):
        _answer_question(lead, text, language, with_hint=False)
        return _ask_next_detail(lead, language, qualification)

    if state == leads.QUALIFY_PROFILE:
        profile = match_profile(text)
        if not profile:
            _answer_question(lead, text, language, with_hint=False)
            _ask_profile(phone, language)
            return language
        qualification["profile"] = profile
        leads.set_qualification(phone, qualification)
        return _educate(lead, language, qualification)

    if state == leads.QUALIFY_NAME:
        qualification["name"] = text
        leads.set_name(phone, text)

    elif state == leads.QUALIFY_AGE:
        age = parse_age(text)
        if age is None:
            wa.send_text(phone, t("qualify_age_retry", language))
            return language
        qualification["age"] = age

    elif state == leads.QUALIFY_EMAIL:
        if not EMAIL_RE.match(text):
            wa.send_text(phone, t("qualify_email_retry", language))
            return language
        qualification["email"] = text

    elif state == leads.QUALIFY_PHONE:
        if len(to_local_phone(text)) != 10:
            wa.send_text(phone, t("qualify_phone_retry", language))
            return language
        qualification["phone"] = text

    elif state == leads.QUALIFY_ADDRESS:
        qualification["address"] = text

    leads.set_qualification(phone, qualification)
    return _ask_next_detail(lead, language, qualification)


def _handle_qualify_tap(lead: dict, action: menus.Action, language: str) -> str:
    phone = lead["phone"]
    qualification = leads.as_json(lead.get("qualification"))

    if action.value == "profile":
        profile = action.course_id or "browsing"
        qualification["profile"] = profile
        leads.set_qualification(phone, qualification)
        # Reached from the menu, after the details are already on file.
        if leads.is_qualified(qualification):
            return _educate(lead, language, qualification)
        return _ask_next_detail(lead, language, qualification)

    if action.value == "age":
        band = action.course_id
        # Bands are stored as a representative age so eligibility maths is uniform.
        age = {"under15": 13, "15_17": 16, "18plus": 18}.get(band, 18)
        qualification["age"] = age
        qualification["age_band"] = band
        leads.set_qualification(phone, qualification)
        return _ask_next_detail(lead, language, qualification)

    _send_main_menu(phone, language)
    return language


# --- stage 3: educate -------------------------------------------------------
def _educate(lead: dict, language: str, qualification: dict) -> str:
    """Show what this specific person can actually take."""
    phone = lead["phone"]
    leads.set_state(phone, leads.EDUCATE)

    age = qualification.get("age")
    profile = qualification.get("profile") or "browsing"

    if age is not None and age < 15:
        wa.send_text(phone, t("educate_under15", language))
        _send_main_menu(phone, language)
        return language

    group = (PROFILES.get(profile) or {}).get("group")
    if not group:
        _send_main_menu(phone, language)
        return language

    courses = catalog.eligible_in_group(group, age)
    if not courses:
        # 15–17 asking for a professional track: fall back to what they can take.
        wa.send_text(phone, t("educate_none_for_profile", language))
        group = "public"
        courses = catalog.eligible_in_group(group, age)

    name = lead.get("name")
    suffix = f" {name.split()[0]}" if name else ""
    wa.send_text(phone, t("educate_intro", language, name=suffix))

    menu = menus.recommended_list(courses, language, offset=0, group=group)
    if menu:
        wa.send_list(phone, menu["body"], menu["button_label"], menu["sections"],
                     header=menu["header"], footer=menu["footer"])
        leads.set_menu_state(phone, {"screen": "group", "group": group, "offset": 0})
    else:
        _send_main_menu(phone, language)
    return language


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


# --- stage 4: propose -------------------------------------------------------
def _propose_course(lead: dict, language: str, course_id: str) -> None:
    """Put one course forward, with its card and a yes/no."""
    phone = lead["phone"]
    course = catalog.get_course(course_id)
    if not course:
        _send_browse_menu(phone, language)
        return

    age = leads.as_json(lead.get("qualification")).get("age")
    card = menus.with_age_warning(menus.course_detail(course, language), course, language, age)

    wa.send_text(phone, card)
    wa.send_buttons(phone, t("propose_question", language),
                    menus.propose_buttons(course_id, language))
    leads.set_state(phone, leads.PROPOSE)
    leads.set_menu_state(phone, {"screen": "propose", "course_id": course_id})


# --- taps -------------------------------------------------------------------
def _handle_tap(lead: dict, message: IncomingMessage, language: str) -> str:
    phone = lead["phone"]
    action = menus.parse_action(message.reply_id)

    if action.kind == "qualify":
        return _handle_qualify_tap(lead, action, language)

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
        _propose_course(lead, language, action.course_id)
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
        _start_confirm(lead, language, course_id=action.course_id or _proposed_course_id(lead))
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
    result = generation.answer(
        text, lead.get("history") or [], language, focus_course_id,
        qualification=leads.as_json(lead.get("qualification")),
    )

    if not result.confident:
        retrieval.log_unanswered(phone, text, result.top_score, language)

    body = f"{result.text}\n\n{t('chat_hint', language)}" if with_hint else result.text
    wa.send_text(phone, body)
    leads.append_history(lead, text, result.text)
    return result.text


# --- stage 5: confirm -------------------------------------------------------
def _start_confirm(lead: dict, language: str, course_id: str | None = None) -> None:
    """Contact details were taken up front, so this only needs the course."""
    phone = lead["phone"]
    qualification = leads.as_json(lead.get("qualification"))
    course = catalog.get_course(course_id) if course_id else None

    if not leads.is_qualified(qualification):
        # Details were never finished (an older lead, or an interrupted start).
        _ask_next_detail(lead, language, qualification)
        return

    if course:
        _complete_enrollment(lead, language, course, qualification)
        return

    leads.set_draft(phone, {})
    leads.set_state(phone, leads.CONFIRM_COURSE)
    wa.send_text(phone, t("confirm_which_course", language))


def _complete_enrollment(lead: dict, language: str, course: dict, qualification: dict) -> None:
    """Everything is known, so the lead goes straight to the dashboard."""
    phone = lead["phone"]
    course_name = course["name_es"] if language == "es" else course["name_en"]
    draft = {
        "course": course_name,
        "course_id": course["course_id"],
        "name": qualification.get("name"),
        "email": qualification.get("email"),
        "phone": qualification.get("phone"),
        "address": qualification.get("address"),
    }

    # State moves before the push so a duplicate delivery cannot double-submit.
    leads.set_state(phone, leads.EDUCATE)
    leads.set_draft(phone, {})
    dashboard.push_lead(phone, draft, language, qualification=qualification)

    first_name = (qualification.get("name") or "").split()
    wa.send_text(phone, t(
        "confirm_done", language,
        name=first_name[0] if first_name else "",
        course=course_name,
        email=qualification.get("email", ""),
        phone=qualification.get("phone", ""),
    ))
    _send_main_menu(phone, language)


def _handle_confirm(lead: dict, text: str, lowered: str, language: str, state: str) -> str:
    phone = lead["phone"]
    draft = leads.as_json(lead.get("enrollment_draft"))

    if any(word in lowered for word in CANCEL_WORDS):
        leads.set_state(phone, leads.EDUCATE)
        leads.set_draft(phone, {})
        wa.send_text(phone, t("enroll_cancelled", language))
        return language

    # Question mid-flow: answer it, then re-ask the field we were on. State and
    # draft are untouched, so nothing collected so far is lost.
    if is_question(text):
        _answer_question(lead, text, language, with_hint=False,
                         focus_course_id=draft.get("course_id"))
        wa.send_text(phone, f"{t('resume_note', language)}\n{_current_prompt(state, language, draft)}")
        log.info("Answered mid-confirm question for %s, resuming at %s", _mask(phone), state)
        return language

    if state == leads.CONFIRM_COURSE:
        qualification = leads.as_json(lead.get("qualification"))
        matches = catalog.search_by_name(text, limit=1)
        if matches:
            _complete_enrollment(lead, language, matches[0], qualification)
            return language
        # Not a course we recognise: keep what they typed so the team can resolve it.
        draft = {"course": text, "name": qualification.get("name"),
                 "email": qualification.get("email"), "phone": qualification.get("phone"),
                 "address": qualification.get("address")}
        leads.set_state(phone, leads.EDUCATE)
        leads.set_draft(phone, {})
        dashboard.push_lead(phone, draft, language, qualification=qualification)
        first_name = (qualification.get("name") or "").split()
        wa.send_text(phone, t(
            "confirm_done", language,
            name=first_name[0] if first_name else "", course=text,
            email=qualification.get("email", ""), phone=qualification.get("phone", ""),
        ))
        _send_main_menu(phone, language)
        return language

    if state == leads.CONFIRM_NAME:
        draft["name"] = text
        leads.set_draft(phone, draft)
        leads.set_name(phone, text)
        leads.set_state(phone, leads.CONFIRM_EMAIL)
        wa.send_text(phone, t("enroll_email", language, name=text.split()[0]))
        return language

    if state == leads.CONFIRM_EMAIL:
        if not EMAIL_RE.match(text):
            wa.send_text(phone, t("enroll_email_retry", language))
            return language
        draft["email"] = text
        leads.set_draft(phone, draft)
        leads.set_state(phone, leads.CONFIRM_PHONE)
        wa.send_text(phone, t("enroll_phone", language))
        return language

    if state == leads.CONFIRM_PHONE:
        if len(to_local_phone(text)) != 10:
            wa.send_text(phone, t("enroll_phone_retry", language))
            return language
        draft["phone"] = text
        leads.set_draft(phone, draft)
        leads.set_state(phone, leads.CONFIRM_ADDRESS)
        wa.send_text(phone, t("enroll_address", language))
        return language

    if state == leads.CONFIRM_ADDRESS:
        draft["address"] = text
        # State moves before the push so a duplicate delivery cannot double-submit.
        leads.set_state(phone, leads.EDUCATE)
        leads.set_draft(phone, {})
        dashboard.push_lead(phone, draft, language,
                            qualification=leads.as_json(lead.get("qualification")))
        wa.send_text(phone, t(
            "enroll_complete", language,
            name=draft.get("name", "").split()[0] if draft.get("name") else "",
            course=draft.get("course", ""),
            email=draft.get("email", ""),
            phone=draft.get("phone", ""),
        ))
        _send_main_menu(phone, language)
        return language

    leads.set_state(phone, leads.EDUCATE)
    return language


def _current_prompt(state: str, language: str, draft: dict) -> str:
    if state == leads.CONFIRM_COURSE:
        return t("enroll_course", language)
    if state == leads.CONFIRM_NAME:
        return t("enroll_name", language, course=draft.get("course", ""))
    if state == leads.CONFIRM_EMAIL:
        first_name = (draft.get("name") or "").split()
        return t("enroll_email", language, name=first_name[0] if first_name else "")
    if state == leads.CONFIRM_PHONE:
        return t("enroll_phone", language)
    return t("enroll_address", language)
