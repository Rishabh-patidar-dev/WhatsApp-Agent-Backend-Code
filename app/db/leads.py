"""Everything the agent stores about a person and where they are in the flow."""
from __future__ import annotations

import json
from typing import Any

from psycopg.types.json import Jsonb

from app.db import client as db

# The conversation runs as five stages: greet, qualify, educate, propose, confirm.
# Each stage owns the states below.
GREET = "GREET"

# Qualify — every detail the training team needs, collected up front so the
# course conversation afterwards is uninterrupted and enrolling is one tap.
QUALIFY_NAME = "QUALIFY_NAME"
QUALIFY_AGE = "QUALIFY_AGE"
QUALIFY_EMAIL = "QUALIFY_EMAIL"
QUALIFY_PHONE = "QUALIFY_PHONE"
QUALIFY_ADDRESS = "QUALIFY_ADDRESS"
QUALIFY_PROFILE = "QUALIFY_PROFILE"  # optional, reachable from the menu

# Educate — browsing and answering questions. The resting state.
EDUCATE = "EDUCATE"

# Propose — a specific course has been put forward and is awaiting a yes.
PROPOSE = "PROPOSE"

# Confirm — collecting the details the training team needs to call them back.
CONFIRM_COURSE = "CONFIRM_COURSE"
CONFIRM_NAME = "CONFIRM_NAME"
CONFIRM_EMAIL = "CONFIRM_EMAIL"
CONFIRM_PHONE = "CONFIRM_PHONE"
CONFIRM_ADDRESS = "CONFIRM_ADDRESS"

# The order the questions are asked in.
QUALIFY_ORDER = (QUALIFY_NAME, QUALIFY_AGE, QUALIFY_EMAIL, QUALIFY_PHONE, QUALIFY_ADDRESS)
QUALIFY_STATES = QUALIFY_ORDER + (QUALIFY_PROFILE,)
# Which copy key asks for each detail.
QUALIFY_PROMPTS = {
    QUALIFY_NAME: "qualify_name",
    QUALIFY_AGE: "qualify_age",
    QUALIFY_EMAIL: "qualify_email",
    QUALIFY_PHONE: "qualify_phone",
    QUALIFY_ADDRESS: "qualify_address",
}
QUALIFY_FIELDS = {
    QUALIFY_NAME: "name",
    QUALIFY_AGE: "age",
    QUALIFY_EMAIL: "email",
    QUALIFY_PHONE: "phone",
    QUALIFY_ADDRESS: "address",
}
CONFIRM_STATES = (CONFIRM_COURSE, CONFIRM_NAME, CONFIRM_EMAIL, CONFIRM_PHONE, CONFIRM_ADDRESS)

# Rows written before the stages were named keep working.
LEGACY_STATES = {
    "CHATTING": EDUCATE,
    "ENROLL_COURSE": CONFIRM_COURSE,
    "ENROLL_NAME": CONFIRM_NAME,
    "ENROLL_EMAIL": CONFIRM_EMAIL,
    "ENROLL_PHONE": CONFIRM_PHONE,
    "ENROLL_ADDRESS": CONFIRM_ADDRESS,
}

MAX_HISTORY_MESSAGES = 20


def normalise_state(state: str | None) -> str:
    return LEGACY_STATES.get(state or "", state or GREET)


def is_qualified(qualification: dict | None) -> bool:
    """True once every detail has been collected."""
    q = qualification or {}
    return all(q.get(field) for field in QUALIFY_FIELDS.values())


def next_qualify_state(qualification: dict | None) -> str | None:
    """The first detail still missing, or None when nothing is."""
    q = qualification or {}
    for state, field in QUALIFY_FIELDS.items():
        if not q.get(field):
            return state
    return None


def reset_for_greeting(phone: str) -> None:
    """A "Hi" is treated as a full reset — this is a POC and must never rely on
    memory of who the person is. Wipes qualification, the enrolment draft and
    menu position, and puts the lead back at the first question."""
    db.execute(
        "UPDATE leads SET qualification = '{}'::jsonb, enrollment_draft = '{}'::jsonb, "
        "menu_state = '{}'::jsonb, state = %s, name = NULL, last_seen_at = NOW() "
        "WHERE phone = %s",
        (QUALIFY_NAME, phone),
    )


def get_or_create(phone: str) -> tuple[dict[str, Any], bool]:
    """Returns (lead, is_new). Atomic, so Meta's duplicate deliveries stay harmless."""
    with db.connection() as conn:
        row = conn.execute(
            "INSERT INTO leads (phone) VALUES (%s) ON CONFLICT (phone) DO NOTHING RETURNING *",
            (phone,),
        ).fetchone()
        if row:
            return row, True
        return conn.execute("SELECT * FROM leads WHERE phone = %s", (phone,)).fetchone(), False


def touch(phone: str) -> None:
    db.execute("UPDATE leads SET last_seen_at = NOW() WHERE phone = %s", (phone,))


def set_state(phone: str, state: str) -> None:
    db.execute(
        "UPDATE leads SET state = %s, last_seen_at = NOW() WHERE phone = %s", (state, phone)
    )


def set_language(phone: str, language: str) -> None:
    db.execute(
        "UPDATE leads SET language = %s, last_seen_at = NOW() WHERE phone = %s", (language, phone)
    )


def set_draft(phone: str, draft: dict) -> None:
    db.execute("UPDATE leads SET enrollment_draft = %s WHERE phone = %s", (Jsonb(draft), phone))


def set_qualification(phone: str, qualification: dict) -> None:
    db.execute(
        "UPDATE leads SET qualification = %s WHERE phone = %s", (Jsonb(qualification), phone)
    )


def set_menu_state(phone: str, menu_state: dict) -> None:
    db.execute("UPDATE leads SET menu_state = %s WHERE phone = %s", (Jsonb(menu_state), phone))


def set_name(phone: str, name: str) -> None:
    db.execute("UPDATE leads SET name = %s WHERE phone = %s", (name, phone))


def save_history(phone: str, history: list[dict]) -> None:
    db.execute(
        "UPDATE leads SET history = %s, last_seen_at = NOW() WHERE phone = %s",
        (Jsonb(history[-MAX_HISTORY_MESSAGES:]), phone),
    )


def append_history(lead: dict, user_text: str, reply_text: str) -> list[dict]:
    history = list(lead.get("history") or [])
    history.append({"role": "user", "text": user_text})
    history.append({"role": "assistant", "text": reply_text})
    save_history(lead["phone"], history)
    return history


def mark_pushed(phone: str, error: str | None = None) -> None:
    db.execute(
        "UPDATE leads SET pushed_to_dashboard = TRUE, dashboard_push_error = %s WHERE phone = %s",
        (error, phone),
    )


def as_json(value: Any) -> dict:
    """psycopg returns jsonb as dict already; this tolerates a string too."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value or {}
