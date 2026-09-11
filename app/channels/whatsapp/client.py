"""Sending messages to WhatsApp through Meta's Cloud API.

Four shapes are used by the agent:
  * text     — normal replies
  * buttons  — up to 3 quick actions under a message
  * list     — the tappable menu (what "More Services" opens in a bank's agent)
  * cta_url  — a single button that opens a link in one tap (the payment page)
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


def send_text(to: str, body: str, preview_url: bool = False) -> bool:
    ok = True
    for part in fmt.split_message(body):
        ok = _post(
            {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": part, "preview_url": preview_url},
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


def send_cta_url(to: str, body: str, button_label: str, url: str,
                 footer: str | None = None) -> bool:
    """One tappable button that opens `url` — no link preview, no copy-paste.

    This is the only way WhatsApp lets a business hand someone a link they can
    act on in a single tap; a business cannot open a browser on someone's phone
    by itself. Meta accepts https only, and rejects the whole message if the
    button label runs past 20 characters.

    Falls back to a plain text message carrying the link if the call-to-action
    message is refused — some numbers and older API versions do not support it,
    and a person halfway through enrolling must never be left with nothing.
    """
    interactive: dict = {
        "type": "cta_url",
        "body": {"text": fmt.clip(body, fmt.INTERACTIVE_BODY_MAX)},
        "action": {
            "name": "cta_url",
            "parameters": {
                "display_text": fmt.clip(button_label, fmt.BUTTON_LABEL_MAX),
                "url": url,
            },
        },
    }
    if footer:
        interactive["footer"] = {"text": fmt.clip(footer, fmt.FOOTER_MAX)}

    if _post(
        {"messaging_product": "whatsapp", "to": to, "type": "interactive",
         "interactive": interactive},
        "send link button",
    ):
        return True

    log.warning("Link button refused — sending the link as text instead")
    return send_text(to, f"{body}\n\n{url}", preview_url=True)


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
