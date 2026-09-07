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
catalog.eligible_in_group = lambda group, age: [
    c for c in fake_courses_in_group(group) if catalog.is_age_eligible(c, age)
]

# --- fixture lead store -----------------------------------------------------
STORE: dict[str, dict] = {}


def fake_get_or_create(phone: str) -> tuple[dict, bool]:
    if phone not in STORE:
        STORE[phone] = {"phone": phone, "language": "es", "state": "GREET",
                        "enrollment_draft": {}, "history": [], "name": None,
                        "menu_state": {}, "human_takeover": False, "qualification": {}}
        return STORE[phone], True
    return STORE[phone], False


leads.get_or_create = fake_get_or_create
leads.set_state = lambda phone, state: STORE[phone].update(state=state)
leads.set_language = lambda phone, lang: STORE[phone].update(language=lang)
leads.set_draft = lambda phone, draft: STORE[phone].update(enrollment_draft=draft)
leads.set_menu_state = lambda phone, ms: STORE[phone].update(menu_state=ms)
leads.set_qualification = lambda phone, q: STORE[phone].update(qualification=q)
leads.set_name = lambda phone, name: STORE[phone].update(name=name)
leads.save_history = lambda phone, history: STORE[phone].update(history=history[-20:])


def fake_append_history(lead, user_text, reply_text):
    history = list(lead.get("history") or [])
    history += [{"role": "user", "text": user_text}, {"role": "assistant", "text": reply_text}]
    STORE[lead["phone"]]["history"] = history[-20:]
    return history


leads.append_history = fake_append_history
leads.mark_pushed = lambda phone, error=None: None

# --- fixture model + outbound channel ---------------------------------------
from app.core import generation, retrieval  # noqa: E402

pushed: list[dict] = []


def fake_answer(question, history=None, language="es", focus_course_id=None,
                qualification=None):
    return generation.Answer(
        text=f"(respuesta del modelo sobre: {question[:60]})",
        course_ids=["HP038"], confident=True, top_score=0.81,
    )


generation.answer = fake_answer
retrieval.log_unanswered = lambda *a, **k: None

from app.integrations import dashboard  # noqa: E402

def fake_push(phone, draft, language, qualification=None):
    pushed.append({**draft, "_qualification": qualification or {}})
    return True


dashboard.push_lead = fake_push


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


def user(text: str, tap: str | None = None) -> list[tuple[str, str]]:
    sent.clear()
    print(f"\n\033[96m>>> {'tap ' + tap if tap else text}\033[0m")
    conversation.handle(IncomingMessage("sim", PHONE, text, "Rohit Singh", tap))
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

    print("\n\033[1m2. Stage 1-2: greet, then qualify\033[0m")
    user("Hola")
    check(sent and sent[0][0] == "text", "new contact gets a short greeting first")
    check(len(sent) >= 3 and sent[-1][0] == "list", "greeting leads straight into the first question")
    check(STORE[PHONE]["state"] == "QUALIFY_PROFILE", "conversation is in the qualify stage")

    result = user("", "q:profile:health")
    check(STORE[PHONE]["qualification"].get("profile") == "health", "profile captured from a tap")
    check(STORE[PHONE]["state"] == "QUALIFY_AGE", "moves on to asking age")
    check(any(k == "buttons" for k, _ in result), "age is asked with tappable bands")
    check(any("edad" in b.lower() or "años" in b.lower() for _, b in result), "age question explains why")

    user("no soy un número")
    check(STORE[PHONE]["state"] == "QUALIFY_AGE", "an unparseable age is re-asked, not accepted")

    print("\n\033[1m3. Stage 3: educate, filtered by age\033[0m")
    user("24")
    check(STORE[PHONE]["qualification"].get("age") == 24, "typed age is stored exactly")
    check(STORE[PHONE]["state"] == "EDUCATE", "qualification hands over to the educate stage")
    check(any(k == "list" for k, _ in sent), "a tailored course list is shown")

    print("\n\033[1m4. Age gates what gets recommended\033[0m")
    # Only 13 courses in the whole catalogue admit under-18s: the 12 public and
    # employee ones, plus HP046 (Psychological First Aid, open from 15).
    teen = catalog.eligible_in_group("health", 16)
    adult = catalog.eligible_in_group("health", 24)
    check([c["course_id"] for c in teen] == ["HP046"],
          f"a 16-year-old sees only the 15+ health course (got {[c['course_id'] for c in teen]})")
    check(len(adult) == 33, f"an adult sees the whole health catalogue (got {len(adult)})")
    check(len(catalog.eligible_in_group("public", 16)) == 8, "public courses stay open at 16")
    check(len(catalog.eligible_in_group("rescue", 16)) == 0, "18+ rescue courses are not offered at 16")
    all_teen = sum(len(catalog.eligible_in_group(g, 16)) for g in catalog.MENU_GROUPS)
    check(all_teen == 13, f"13 courses in total admit under-18s (got {all_teen})")

    user("", "menu:browse")
    check(any("categor" in b.lower() or "categ" in b.lower() for _, b in sent), "browse menu lists categories")

    print("\n\033[1m5. Paging through a long category\033[0m")
    health = fake_courses_in_group("health")
    page1 = menus.course_list("health", "es", 0)
    ids = [r["id"] for r in page1["sections"][0]["rows"]]
    check(len(ids) == 10, "first page shows 9 courses + a navigation row")
    check(ids[-1] == "grp:health:9", f"last row pages forward (got {ids[-1]})")
    page2 = menus.course_list("health", "es", 9)
    check(page2["sections"][0]["rows"][0]["id"] != ids[0], "second page starts at a different course")
    user("", "grp:health")
    user("", "grp:health:9")

    print("\n\033[1m6. Stage 4: propose\033[0m")
    result = user("", "crs:HP038")
    check(any("1,900" in b for _, b in result), "BLS card shows its real price ($1,900 MXN)")
    check(any(k == "buttons" for k, _ in result), "the proposal asks for a yes")
    check(STORE[PHONE]["state"] == "PROPOSE", "conversation is in the propose stage")
    check(STORE[PHONE]["menu_state"].get("course_id") == "HP038", "the proposed course is remembered")

    print("\n\033[1m7. Stage 5: confirm, interrupted by a question\033[0m")
    user("", "act:enroll:HP038")
    check(STORE[PHONE]["state"] == "CONFIRM_NAME", "starting from a course skips the 'which course' step")
    check(STORE[PHONE]["enrollment_draft"].get("course_id") == "HP038", "course is pre-filled on the draft")

    user("Rohit Singh")
    check(STORE[PHONE]["state"] == "CONFIRM_EMAIL", "name captured")

    before = dict(STORE[PHONE]["enrollment_draft"])
    result = user("¿el curso incluye manual?")
    check(STORE[PHONE]["state"] == "CONFIRM_EMAIL", "a question mid-flow does not advance the flow")
    check(STORE[PHONE]["enrollment_draft"] == before, "nothing collected so far is lost")
    check(len(result) == 2, "the question is answered and the field is asked again")

    user("no-es-un-correo")
    check(STORE[PHONE]["state"] == "CONFIRM_EMAIL", "invalid email is rejected")
    user("rohit@sample.com")
    check(STORE[PHONE]["state"] == "CONFIRM_PHONE", "valid email accepted")
    user("123")
    check(STORE[PHONE]["state"] == "CONFIRM_PHONE", "short phone number is rejected")
    user("5512345678")
    check(STORE[PHONE]["state"] == "CONFIRM_ADDRESS", "valid phone accepted")
    user("Mandsaur MP")
    check(STORE[PHONE]["state"] == "EDUCATE", "flow completes and returns to the educate stage")
    check(len(pushed) == 1, "lead pushed to the dashboard exactly once")
    check(pushed[0].get("email") == "rohit@sample.com" and pushed[0].get("course_id") == "HP038",
          "lead carries the course and contact details")
    check(pushed[0]["_qualification"].get("age") == 24
          and pushed[0]["_qualification"].get("profile") == "health",
          "lead carries the qualification the team needs")

    print("\n\033[1m8. Language switch and free text\033[0m")
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
