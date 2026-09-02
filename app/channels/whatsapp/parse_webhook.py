"""Reading Meta's incoming webhook format.

Messages arrive in batches, and delivery receipts ("your message was read")
arrive on the same URL mixed in with real messages, so this sorts out which is
which and hands back only things a human actually sent.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IncomingMessage:
    message_id: str
    from_phone: str
    text: str
    contact_name: str | None = None
    # Set when the person tapped a menu row or a button instead of typing.
    reply_id: str | None = None

    @property
    def is_tap(self) -> bool:
        return self.reply_id is not None


def parse(payload: dict) -> list[IncomingMessage]:
    messages: list[IncomingMessage] = []

    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}

            # Status callbacks (sent/delivered/read) carry no "messages" key.
            contacts = value.get("contacts") or []
            contact_name = None
            if contacts:
                contact_name = (contacts[0].get("profile") or {}).get("name")

            for message in value.get("messages", []) or []:
                kind = message.get("type")
                message_id = message.get("id", "")
                sender = message.get("from", "")
                if not sender:
                    continue

                if kind == "text":
                    body = (message.get("text") or {}).get("body", "")
                    messages.append(IncomingMessage(message_id, sender, body, contact_name))

                elif kind == "interactive":
                    interactive = message.get("interactive") or {}
                    reply = interactive.get("list_reply") or interactive.get("button_reply") or {}
                    if reply:
                        messages.append(IncomingMessage(
                            message_id, sender, reply.get("title", ""), contact_name,
                            reply_id=reply.get("id"),
                        ))

                elif kind == "button":
                    # Template quick-reply buttons post under a different type.
                    button = message.get("button") or {}
                    messages.append(IncomingMessage(
                        message_id, sender, button.get("text", ""), contact_name,
                        reply_id=button.get("payload"),
                    ))

                else:
                    # Images, audio, documents, location: acknowledged politely
                    # by the conversation layer rather than parsed here.
                    messages.append(IncomingMessage(
                        message_id, sender, "", contact_name, reply_id=f"unsupported:{kind}"
                    ))

    return messages
