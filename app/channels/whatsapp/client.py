"""Sending messages to WhatsApp through Meta's Cloud API.

Three shapes are used by the agent:
  * text     — normal replies
  * buttons  — up to 3 quick actions under a message
  * list     — the tappable menu (what "More Services" opens in a bank's agent)
"""
from __future__ import annotations

import logging
from typing import Iterable

import requests

from app.channels.whatsapp import format as fmt
from app.config import settings

log = logging.getLogger(__name__)

_session = requests.Session()


def _post(payload: dict, action: str) -> bool:
    try:
        response = _session.post(
            settings.meta_messages_url,
            headers={
                "Authorization": f"Bearer {settings.meta_access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=settings.http_timeout_seconds,
        )
    except requests.RequestException as exc:
        log.error("Failed to %s: %r", action, exc)
        return False

    if not response.ok:
        # Never log the payload itself — it contains the customer's message.
        log.error("Failed to %s: %s %s", action, response.status_code, response.text[:400])
        return False
    return True


def send_text(to: str, body: str) -> bool:
    ok = True
    for part in fmt.split_message(body):
        ok = _post(
            {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": part, "preview_url": False},
            },
            "send text",
        ) and ok
    return ok


def send_buttons(to: str, body: str, buttons: Iterable[tuple[str, str]]) -> bool:
    """buttons: (id, label) pairs, at most 3."""
    rows = list(buttons)[: fmt.MAX_BUTTONS]
    return _post(
        {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": fmt.clip(body, fmt.INTERACTIVE_BODY_MAX)},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": bid, "title": fmt.clip(label, fmt.BUTTON_LABEL_MAX)}}
                        for bid, label in rows
                    ]
                },
            },
        },
        "send buttons",
    )


def send_list(
    to: str,
    body: str,
    button_label: str,
    sections: list[dict],
    header: str | None = None,
    footer: str | None = None,
) -> bool:
    """sections: [{"title": str, "rows": [{"id", "title", "description"}]}].

    Meta allows 10 rows across all sections combined; anything beyond that is
    dropped here rather than being rejected by the API.
    """
    budget = fmt.MAX_ROWS_PER_LIST
    payload_sections = []
    for section in sections:
        if budget <= 0:
            break
        rows = section.get("rows", [])[:budget]
        if not rows:
            continue
        budget -= len(rows)
        payload_sections.append({
            "title": fmt.clip(section.get("title", ""), fmt.SECTION_TITLE_MAX),
            "rows": [
                {
                    "id": row["id"][:200],
                    "title": fmt.clip(row["title"], fmt.ROW_TITLE_MAX),
                    **({"description": fmt.clip(row["description"], fmt.ROW_DESCRIPTION_MAX)}
                       if row.get("description") else {}),
                }
                for row in rows
            ],
        })

    interactive: dict = {
        "type": "list",
        "body": {"text": fmt.clip(body, fmt.INTERACTIVE_BODY_MAX)},
        "action": {
            "button": fmt.clip(button_label, fmt.BUTTON_LABEL_MAX),
            "sections": payload_sections,
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": fmt.clip(header, fmt.HEADER_MAX)}
    if footer:
        interactive["footer"] = {"text": fmt.clip(footer, fmt.FOOTER_MAX)}

    return _post(
        {"messaging_product": "whatsapp", "to": to, "type": "interactive", "interactive": interactive},
        "send list",
    )


def mark_read(message_id: str) -> None:
    """Blue ticks, so the person can see the agent picked the message up."""
    _post(
        {"messaging_product": "whatsapp", "status": "read", "message_id": message_id},
        "mark read",
    )
