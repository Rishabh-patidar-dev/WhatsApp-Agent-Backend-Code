"""Chat model provider.

`core/` asks for "something that can turn a prompt into text" and this decides
what that is. Today Gemini; setting CHAT_PROVIDER=claude switches to Anthropic
without touching a line of logic anywhere else.
"""
from __future__ import annotations

import logging
import os

from langchain_core.language_models import BaseChatModel

from app.config import settings

log = logging.getLogger(__name__)


def build_chat_model() -> BaseChatModel:
    provider = os.environ.get("CHAT_PROVIDER", "gemini").strip().lower()

    if provider == "claude":
        from langchain_anthropic import ChatAnthropic

        log.info("Chat provider: anthropic")
        return ChatAnthropic(model="claude-haiku-4-5", max_tokens=600, timeout=30)

    from langchain_google_genai import ChatGoogleGenerativeAI

    log.info("Chat provider: gemini (%s)", settings.chat_model)
    return ChatGoogleGenerativeAI(
        model=settings.chat_model,
        google_api_key=settings.gemini_api_key,
        max_output_tokens=600,
        timeout=30,
    )


_model: BaseChatModel | None = None


def chat_model() -> BaseChatModel:
    """Built once, on first use, so imports stay side-effect free."""
    global _model
    if _model is None:
        _model = build_chat_model()
    return _model
