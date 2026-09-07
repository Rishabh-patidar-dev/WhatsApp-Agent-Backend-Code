"""Drives a full conversation offline and prints the transcript.

Nothing is sent to WhatsApp: the Meta client is swapped for a recorder, so this
exercises the real menus, the real retrieval and the real sign-up flow against
the real database without messaging anyone.

    python scripts/simulate_chat.py

Menu taps are written as `>> tap crs:HP038`; anything else is typed text.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.channels.whatsapp import client as wa  # noqa: E402
from app.channels.whatsapp.parse_webhook import IncomingMessage  # noqa: E402

TEST_PHONE = "5215500000000"  # never messaged; only used as a database key

# --- swap the Meta client for a recorder ------------------------------------
transcript: list[str] = []


def _record_text(to: str, body: str) -> bool:
    transcript.append(f"[text]\n{body}")
    return True


def _record_buttons(to: str, body: str, buttons) -> bool:
    labels = " | ".join(f"[{label}]" for _, label in buttons)
    transcript.append(f"[buttons] {body}\n  {labels}")
    return True


def _record_list(to, body, button_label, sections, header=None, footer=None) -> bool:
    lines = [f"[list] {header or ''}", body, f"  ({button_label} ▾)"]
    for section in sections:
        lines.append(f"  — {section['title']} —")
        for row in section["rows"]:
            desc = f"   · {row.get('description', '')}" if row.get("description") else ""
            lines.append(f"    • {row['title']:<26} {row['id']}{desc}")
    transcript.append("\n".join(lines))
    return True


wa.send_text = _record_text
wa.send_buttons = _record_buttons
wa.send_list = _record_list

from app.core import conversation  # noqa: E402  (imported after the swap)
from app.db import client as db  # noqa: E402


def send(text: str, tap: str | None = None) -> None:
    who = f"tap {tap}" if tap else text
    print(f"\n\033[92m>>> USER: {who}\033[0m")
    transcript.clear()
    conversation.handle(
        IncomingMessage(
            message_id="sim", from_phone=TEST_PHONE, text=text,
            contact_name="Rohit Singh", reply_id=tap,
        )
    )
    for block in transcript:
        print(block)


def reset() -> None:
    db.execute("DELETE FROM leads WHERE phone = %s", (TEST_PHONE,))


SCRIPTED = [
    ("Hola", None),                                # greet
    ("Rohit Singh", None),                         # 1 of 5 - name
    ("24", None),                                  # 2 of 5 - age
    ("rohit@sample.com", None),                    # 3 of 5 - email
    ("5512345678", None),                          # 4 of 5 - phone
    ("Mandsaur MP", None),                         # 5 of 5 - address, then the menu
    ("¿cuánto cuesta el BLS?", None),              # educate
    ("", "crs:HP038"),                             # propose
    ("", "act:enroll:HP038"),                      # confirm - one tap
]


def main() -> None:
    reset()
    for text, tap in SCRIPTED:
        send(text, tap)
    lead = db.query_one("SELECT state, enrollment_draft, pushed_to_dashboard, dashboard_push_error "
                        "FROM leads WHERE phone = %s", (TEST_PHONE,))
    print(f"\n\033[93m--- final lead row ---\033[0m\n{lead}")


if __name__ == "__main__":
    main()
