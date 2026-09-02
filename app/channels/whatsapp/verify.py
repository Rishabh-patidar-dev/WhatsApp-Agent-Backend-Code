"""Proving an incoming webhook really came from Meta.

The webhook URL is public: anyone who finds it can POST a message that looks
like it came from a customer. Meta signs every delivery with an HMAC over the
exact raw bytes it sent, keyed on the app secret, so checking that signature is
what separates a real customer from a stranger with curl.

The comparison must be constant-time, and the digest must be computed over the
*raw* body — re-serialising the parsed JSON changes the bytes and breaks it.
"""
from __future__ import annotations

import hashlib
import hmac
import logging

from app.config import settings

log = logging.getLogger(__name__)

SIGNATURE_HEADER = "x-hub-signature-256"


def is_valid_signature(raw_body: bytes, header_value: str | None) -> bool:
    if not settings.signature_verification_enabled:
        # Loud, because running without this in production leaves the endpoint
        # spoofable by anyone who learns the URL.
        log.warning(
            "META_APP_SECRET is not set — webhook signatures are NOT being verified. "
            "Set it from Meta > App > Settings > Basic before going live."
        )
        return True

    if not header_value or not header_value.startswith("sha256="):
        log.warning("Rejected webhook: missing or malformed %s header", SIGNATURE_HEADER)
        return False

    expected = hmac.new(
        settings.meta_app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, header_value.split("=", 1)[1].strip()):
        log.warning("Rejected webhook: signature mismatch")
        return False
    return True
