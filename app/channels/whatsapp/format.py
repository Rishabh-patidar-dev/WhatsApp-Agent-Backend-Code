"""WhatsApp's formatting and length limits, in one place.

Meta rejects the whole message if any field is one character too long, so every
string that goes into a payload passes through here first.
"""
from __future__ import annotations

# Hard limits from the Cloud API reference.
TEXT_BODY_MAX = 4096
INTERACTIVE_BODY_MAX = 1024
HEADER_MAX = 60
FOOTER_MAX = 60
BUTTON_LABEL_MAX = 20
ROW_TITLE_MAX = 24
ROW_DESCRIPTION_MAX = 72
SECTION_TITLE_MAX = 24
MAX_ROWS_PER_LIST = 10
MAX_BUTTONS = 3


def clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def split_message(text: str, limit: int = TEXT_BODY_MAX) -> list[str]:
    """Splits an over-long message on paragraph, then line, then word boundaries."""
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []

    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(" "))
        if cut <= 0:
            cut = limit
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts
