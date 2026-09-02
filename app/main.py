"""The front door — the only part of the system exposed to the internet.

It deliberately does almost no work. Meta gives roughly two seconds to respond
before it assumes the endpoint is broken and redelivers the message, and a real
answer takes three to five seconds (embed, search, verify, generate). So the
webhook verifies the message is genuine, hands it to a background task, and
replies immediately.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response

from app.channels.whatsapp import verify
from app.channels.whatsapp.parse_webhook import parse
from app.config import settings
from app.core import conversation
from app.db import catalog
from app.db import client as db

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("app")

@asynccontextmanager
async def lifespan(_: FastAPI):
    if not settings.signature_verification_enabled:
        log.warning("META_APP_SECRET not set — webhook signature verification is DISABLED")
    try:
        # The HNSW index is invisible when missing: search still works, just
        # slowly and expensively. Assert it on boot so it cannot rot silently.
        found = db.query_one(
            "SELECT 1 AS ok FROM pg_indexes WHERE indexname = 'course_chunks_embedding_idx'"
        )
        if not found:
            log.warning("Vector index course_chunks_embedding_idx is MISSING — run schema.sql")
        log.info("Catalogue loaded: %s courses", catalog.total_courses())
    except Exception as exc:
        log.error("Startup database check failed: %r", exc)
    yield
    db.close_pool()


app = FastAPI(title="Cruz Roja WhatsApp Agent", version="2.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/webhook")
def verify_webhook(request: Request):
    """Meta calls this once, when the webhook is first configured."""
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == settings.meta_verify_token:
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(status_code=403, detail="Bad verification token")


@app.post("/webhook")
async def receive_webhook(
    request: Request,
    background: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
):
    # The signature is computed over the exact bytes Meta sent, so the raw body
    # has to be read before anything parses it.
    raw = await request.body()
    if not verify.is_valid_signature(raw, x_hub_signature_256):
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = await request.json()
    except ValueError:
        return {"status": "ignored"}

    for message in parse(payload):
        background.add_task(conversation.handle, message)

    return {"status": "ok"}
