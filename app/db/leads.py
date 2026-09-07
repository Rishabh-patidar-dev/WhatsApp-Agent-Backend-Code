"""Everything the agent stores about a person and where they are in the flow."""
from __future__ import annotations

import json
from typing import Any

from psycopg.types.json import Jsonb

from app.db import client as db

# The conversation runs as five stages: greet, qualify, educate, propose, confirm.
# Each stage owns the states below.
GREET = "GREET"

# Qualify — who is asking, and how old are they (the catalogue gates at 15/18).
QUALIFY_PROFILE = "QUALIFY_PROFILE"
QUALIFY_AGE = "QUALIFY_AGE"

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

QUALIFY_STATES = (QUALIFY_PROFILE, QUALIFY_AGE)
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
