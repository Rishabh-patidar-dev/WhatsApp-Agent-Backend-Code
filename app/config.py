"""The only module in the project allowed to read environment variables.

Everything else imports `settings` from here. Validation happens once at import
time so a missing key fails loudly at boot instead of at 2am on a live webhook.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

_REQUIRED = (
    "META_ACCESS_TOKEN",
    "META_PHONE_NUMBER_ID",
    "META_VERIFY_TOKEN",
    "DATABASE_URL",
    "GEMINI_API_KEY",
)


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = _optional(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # --- Meta WhatsApp Cloud API ---
    meta_access_token: str
    meta_phone_number_id: str
    meta_verify_token: str
    # App Secret signs every webhook Meta sends. Without it we cannot prove an
    # incoming message is genuine, so the endpoint is spoofable — see
    # channels/whatsapp/verify.py.
    meta_app_secret: str
    meta_api_version: str

    # --- Data ---
    database_url: str

    # --- Models ---
    gemini_api_key: str
    embedding_model: str
    embedding_dim: int
    chat_model: str

    # --- Dashboard lead push (optional) ---
    dashboard_url: str
    ingest_token: str

    # --- Behaviour / limits ---
    log_level: str
    max_user_chars: int
    rate_limit_messages: int
    rate_limit_window_seconds: int
    history_turns: int
    retrieval_k: int
    http_timeout_seconds: int
    db_pool_max: int

    _redacted: tuple[str, ...] = field(
        default=("meta_access_token", "meta_app_secret", "database_url", "gemini_api_key", "ingest_token"),
        repr=False,
    )

    @property
    def meta_messages_url(self) -> str:
        return (
            f"https://graph.facebook.com/{self.meta_api_version}"
            f"/{self.meta_phone_number_id}/messages"
        )

    @property
    def dashboard_push_enabled(self) -> bool:
        return bool(self.dashboard_url and self.ingest_token)

    @property
    def signature_verification_enabled(self) -> bool:
        return bool(self.meta_app_secret)


def _load() -> Settings:
    for name in _REQUIRED:
        _require(name)
    return Settings(
        meta_access_token=_require("META_ACCESS_TOKEN"),
        meta_phone_number_id=_require("META_PHONE_NUMBER_ID"),
        meta_verify_token=_require("META_VERIFY_TOKEN"),
        meta_app_secret=_optional("META_APP_SECRET"),
        meta_api_version=_optional("META_API_VERSION", "v20.0"),
        database_url=_require("DATABASE_URL"),
        gemini_api_key=_require("GEMINI_API_KEY"),
        embedding_model=_optional("EMBEDDING_MODEL", "gemini-embedding-001"),
        embedding_dim=_int("EMBEDDING_DIM", 768),
        chat_model=_optional("CHAT_MODEL", "gemini-3.5-flash-lite"),
        dashboard_url=_optional("DASHBOARD_URL").rstrip("/"),
        ingest_token=_optional("INGEST_TOKEN"),
        log_level=_optional("LOG_LEVEL", "INFO").upper(),
        max_user_chars=_int("MAX_USER_CHARS", 1000),
        rate_limit_messages=_int("RATE_LIMIT_MESSAGES", 20),
        rate_limit_window_seconds=_int("RATE_LIMIT_WINDOW_SECONDS", 60),
        history_turns=_int("HISTORY_TURNS", 6),
        retrieval_k=_int("RETRIEVAL_K", 5),
        http_timeout_seconds=_int("HTTP_TIMEOUT_SECONDS", 10),
        db_pool_max=_int("DB_POOL_MAX", 5),
    )


settings = _load()
