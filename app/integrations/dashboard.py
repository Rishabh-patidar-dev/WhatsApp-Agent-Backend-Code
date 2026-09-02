"""Pushing a completed sign-up to the Cruz Roja Records Dashboard.

Fire-and-forget by design: if the dashboard is down, the lead is still saved in
our own database and the failure reason is written to the lead row, so it can
be found with a query instead of by reading deploy logs.
"""
from __future__ import annotations

import logging

import requests

from app.config import settings
from app.db import leads

log = logging.getLogger(__name__)

COMMENT_MAX = 2000


def push_lead(phone: str, draft: dict, language: str) -> bool:
    if not settings.dashboard_push_enabled:
        leads.mark_pushed(
            phone,
            error="Not configured: DASHBOARD_URL or INGEST_TOKEN missing on this deployment",
        )
        return False

    course = draft.get("course", "N/A")
    course_id = draft.get("course_id")
    course_line = f"{course} (clave {course_id})" if course_id else course

    comment = (
        f"Curso de interés: {course_line}. "
        f"Dirección: {draft.get('address', 'N/A')}. "
        f"Solicitado por el agente de WhatsApp. Idioma preferido: {language}."
    )[:COMMENT_MAX]

    payload = {
        "name": draft.get("name") or "WhatsApp Lead",
        "email": draft.get("email", ""),
        "phone": draft.get("phone") or phone,
        "comment": comment,
    }

    error: str | None = None
    try:
        response = requests.post(
            f"{settings.dashboard_url}/api/public/leads",
            headers={"x-ingest-token": settings.ingest_token, "Content-Type": "application/json"},
            json=payload,
            timeout=settings.http_timeout_seconds,
        )
        if not response.ok:
            error = f"HTTP {response.status_code}: {response.text[:400]}"
            log.error("Dashboard rejected lead: %s", error)
    except requests.RequestException as exc:
        error = f"Request failed: {exc}"
        log.error("Dashboard push failed: %r", exc)

    leads.mark_pushed(phone, error=error)
    return error is None
