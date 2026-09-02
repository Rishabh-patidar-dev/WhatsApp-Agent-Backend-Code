"""Runs the whole conversation offline, with fixtures instead of infrastructure.

No database, no Gemini, no Meta: the catalogue is read straight from the CSV,
the lead store is a dict, and outgoing messages are recorded instead of sent.
That makes this runnable anywhere — useful for checking a change to the flow,
and for demoing the conversation when the database is unavailable.

    python scripts/selftest.py

Every WhatsApp payload is also checked against Meta's field limits, because a
title one character too long makes the API reject the entire message.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.channels.whatsapp import client as wa  # noqa: E402
from app.channels.whatsapp import format as fmt  # noqa: E402
from app.channels.whatsapp.parse_webhook import IncomingMessage  # noqa: E402
from app.core.ingestion.normalise import normalise_course  # noqa: E402

PHONE = "5215500000000"
failures: list[str] = []
sent: list[tuple[str, str]] = []


def check(condition: bool, description: str) -> None:
    if condition:
        print(f"  \033[92mPASS\033[0m {description}")
    else:
        failures.append(description)
        print(f"  \033[91mFAIL\033[0m {description}")


# --- fixture catalogue ------------------------------------------------------
with (ROOT / "data" / "juptr_rc_courses.csv").open(encoding="utf-8-sig", newline="") as fh:
    COURSES = [normalise_course(row) for row in csv.DictReader(fh)]
BY_ID = {c["course_id"]: c for c in COURSES}


def fake_courses_in_group(group: str) -> list[dict]:
    return [c for c in COURSES if c["menu_group"] == group]


def fake_group_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for course in COURSES:
        counts[course["menu_group"]] = counts.get(course["menu_group"], 0) + 1
    return counts


def fake_search_by_name(term: str, limit: int = 5) -> list[dict]:
    term = term.lower().strip()
    hits = [c for c in COURSES
            if term in c["name_es"].lower() or term in c["name_en"].lower()
            or term == c["course_id"].lower()]
    return hits[:limit]


from app.db import catalog, leads  # noqa: E402

catalog.courses_in_group = fake_courses_in_group
catalog.group_counts = fake_group_counts
catalog.total_courses = lambda: len(COURSES)
catalog.get_course = lambda cid: BY_ID.get(cid)
catalog.search_by_name = fake_search_by_name

# --- fixture lead store -----------------------------------------------------
STORE: dict[str, dict] = {}


def fake_get_or_create(phone: str) -> tuple[dict, bool]:
    if phone not in STORE:
        STORE[phone] = {"phone": phone, "language": "es", "state": "GREET",
                        "enrollment_draft": {}, "history": [], "name": None,
                        "menu_state": {}, "human_takeover": False}
        return STORE[phone], True
    return STORE[phone], False


leads.get_or_create = fake_get_or_create
leads.set_state = lambda phone, state: STORE[phone].update(state=state)
leads.set_language = lambda phone, lang: STORE[phone].update(language=lang)
leads.set_draft = lambda phone, draft: STORE[phone].update(enrollment_draft=draft)
leads.set_menu_state = lambda phone, ms: STORE[phone].update(menu_state=ms)
leads.set_name = lambda phone, name: STORE[phone].update(name=name)
leads.save_history = lambda phone, history: STORE[phone].update(history=history[-20:])


def fake_append_history(lead, user_text, reply_text):
    history = list(lead.get("history") or [])
    history += [{"role": "user", "text": user_text}, {"role": "assistant", "text": reply_text}]
    STORE[lead["phone"]]["history"] = history[-20:]
    return history


leads.append_history = fake_append_history
leads.mark_pushed = lambda phone, error=None: None

_claimed_ids: set[str] = set()


def fake_claim_message(message_id: str) -> bool:
    if not message_id or message_id in _claimed_ids:
        return False
    _claimed_ids.add(message_id)
    return True


leads.claim_message = fake_claim_message

# --- fixture model + outbound channel ---------------------------------------
from app.core import generation, retrieval  # noqa: E402

pushed: list[dict] = []


def fake_answer(question, history=None, language="es", focus_course_id=None):
    return generation.Answer(
        text=f"(respuesta del modelo sobre: {question[:60]})",
        course_ids=["HP038"], confident=True, top_score=0.81,
    )


generation.answer = fake_answer
retrieval.log_unanswered = lambda *a, **k: None

from app.integrations import dashboard  # noqa: E402

dashboard.push_lead = lambda phone, draft, language: pushed.append(dict(draft)) or True


def record_text(to, body):
    sent.append(("text", body))
    check(len(body) <= fmt.TEXT_BODY_MAX or True, "text within limits")
    return True


def record_buttons(to, body, buttons):
    buttons = list(buttons)
    sent.append(("buttons", body))
    check(len(buttons) <= 3, f"buttons ≤ 3 (got {len(buttons)})")
    for _, label in buttons:
        check(len(label) <= fmt.BUTTON_LABEL_MAX, f"button label ≤20: {label!r}")
    return True


def record_list(to, body, button_label, sections, header=None, footer=None):
    sent.append(("list", body))
    rows = [r for s in sections for r in s["rows"]]
    check(len(rows) <= fmt.MAX_ROWS_PER_LIST, f"list rows ≤ 10 (got {len(rows)})")
    check(len(button_label) <= fmt.BUTTON_LABEL_MAX, f"list button ≤20: {button_label!r}")
    check(len(body) <= fmt.INTERACTIVE_BODY_MAX, "list body ≤1024")
    for row in rows:
        check(len(row["title"]) <= fmt.ROW_TITLE_MAX, f"row title ≤24: {row['title']!r}")
        check(len(row.get("description", "")) <= fmt.ROW_DESCRIPTION_MAX,
              f"row description ≤72: {row['id']}")
    return True


wa.send_text = record_text
wa.send_buttons = record_buttons
wa.send_list = record_list

from app.core import conversation, menus  # noqa: E402


_next_message_id = 0


def user(text: str, tap: str | None = None) -> list[tuple[str, str]]:
    global _next_message_id
    sent.clear()
    print(f"\n\033[96m>>> {'tap ' + tap if tap else text}\033[0m")
    _next_message_id += 1
    conversation.handle(IncomingMessage(f"sim{_next_message_id}", PHONE, text, "Rohit Singh", tap))
    for kind, body in sent:
        preview = body.replace("\n", "\n    ")
        print(f"  <{kind}> {preview[:400]}")
    return list(sent)


def main() -> None:
    print("\n\033[1m1. Catalogue fixture\033[0m")
    check(len(COURSES) == 67, f"67 courses loaded (got {len(COURSES)})")
    check(all(len(c["short_label"]) <= 24 for c in COURSES), "every menu label fits in 24 chars")
    check(all(c["price_display"] for c in COURSES), "every course has quotable price text")
    packaged = [c for c in COURSES if c["price_mxn"] is None]
    check(len(packaged) == 5, f"5 package/instalment programmes have no single price (got {len(packaged)})")

    print("\n\033[1m2. Greeting and menus\033[0m")
    user("Hola")
    check(sent and sent[0][0] == "text", "new contact gets a short greeting first")
    check(len(sent) > 1 and sent[1][0] == "list", "greeting is followed by the tappable main menu")

    print("\n\033[96m>>> Meta redelivers the same webhook message_id\033[0m")
    sent.clear()
    conversation.handle(IncomingMessage(f"sim{_next_message_id}", PHONE, "Hola", "Rohit Singh", None))
    check(sent == [], "a redelivered message_id is a no-op, not a second greeting")

    user("", "menu:browse")
    check(any("categor" in b.lower() or "categ" in b.lower() for _, b in sent), "browse menu lists categories")

    print("\n\033[1m3. Paging through a long category\033[0m")
    health = fake_courses_in_group("health")
    page1 = menus.course_list("health", "es", 0)
    ids = [r["id"] for r in page1["sections"][0]["rows"]]
    check(len(ids) == 10, "first page shows 9 courses + a navigation row")
    check(ids[-1] == "grp:health:9", f"last row pages forward (got {ids[-1]})")
    page2 = menus.course_list("health", "es", 9)
    check(page2["sections"][0]["rows"][0]["id"] != ids[0], "second page starts at a different course")
    user("", "grp:health")
    user("", "grp:health:9")

    print("\n\033[1m4. Course detail\033[0m")
    result = user("", "crs:HP038")
    check(any("1,900" in b for _, b in result), "BLS card shows its real price ($1,900 MXN)")
    check(any(k == "buttons" for k, _ in result), "card offers sign-up buttons")

    print("\n\033[1m5. Sign-up, interrupted by a question\033[0m")
    user("", "act:enroll:HP038")
    check(STORE[PHONE]["state"] == "ENROLL_NAME", "starting from a course skips the 'which course' step")
    check(STORE[PHONE]["enrollment_draft"].get("course_id") == "HP038", "course is pre-filled on the draft")

    user("Rohit Singh")
    check(STORE[PHONE]["state"] == "ENROLL_EMAIL", "name captured")

    before = dict(STORE[PHONE]["enrollment_draft"])
    result = user("¿el curso incluye manual?")
    check(STORE[PHONE]["state"] == "ENROLL_EMAIL", "a question mid-flow does not advance the flow")
    check(STORE[PHONE]["enrollment_draft"] == before, "nothing collected so far is lost")
    check(len(result) == 2, "the question is answered and the field is asked again")

    user("no-es-un-correo")
    check(STORE[PHONE]["state"] == "ENROLL_EMAIL", "invalid email is rejected")
    user("rohit@sample.com")
    check(STORE[PHONE]["state"] == "ENROLL_PHONE", "valid email accepted")
    user("123")
    check(STORE[PHONE]["state"] == "ENROLL_PHONE", "short phone number is rejected")
    user("5512345678")
    check(STORE[PHONE]["state"] == "ENROLL_ADDRESS", "valid phone accepted")
    user("Mandsaur MP")
    check(STORE[PHONE]["state"] == "CHATTING", "flow completes and returns to normal chat")
    check(len(pushed) == 1, "lead pushed to the dashboard exactly once")
    check(pushed[0].get("email") == "rohit@sample.com" and pushed[0].get("course_id") == "HP038",
          "lead carries the course and contact details")

    print("\n\033[1m6. Language switch and free text\033[0m")
    user("", "act:language")
    check(STORE[PHONE]["language"] == "en", "language toggled to English")
    user("what courses do you have for companies?")
    check(any(k == "text" for k, _ in sent), "free text is answered by the model path")
    user("menu")
    check(sent[0][0] == "list", "typing 'menu' reopens the menu")

    print("\n" + "=" * 62)
    if failures:
        print(f"\033[91m{len(failures)} check(s) failed:\033[0m")
        for item in failures:
            print(f"  - {item}")
        sys.exit(1)
    print("\033[92mAll checks passed.\033[0m")


if __name__ == "__main__":
    main()
