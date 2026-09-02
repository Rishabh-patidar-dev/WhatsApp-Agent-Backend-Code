"""Writing the answer — retrieve, assemble context, generate, verify.

`answer()` is the single entry point. WhatsApp calls it; a future web widget
would call the same function with the same arguments.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.config import settings
from app.core import prompts, retrieval
from app.db import catalog
from app.providers.llm import chat_model

log = logging.getLogger(__name__)

MONEY_RE = re.compile(r"\$\s?([\d][\d,\.]*)")


@dataclass
class Answer:
    text: str
    course_ids: list[str]
    confident: bool
    top_score: float


def _history_text(history: list[dict], language: str) -> str:
    if not history:
        return "(sin mensajes previos)" if language == "es" else "(no previous messages)"
    turns = history[-settings.history_turns :]
    return "\n".join(
        f"{'Usuario' if t.get('role') == 'user' else 'Asistente'}: {t.get('text', '')}"
        for t in turns
    )


def _notices(docs: list[Document], language: str) -> list[str]:
    """Compliance rules attached to the specific courses that were retrieved."""
    seen: set[str] = set()
    notices: list[str] = []
    for doc in docs:
        course_id = doc.metadata.get("course_id")
        if not course_id:
            continue
        course = catalog.get_course(course_id)
        for flag in (course or {}).get("compliance_flags", "").split(";"):
            name = flag.split(":")[0].strip()
            if name and name not in seen and name in prompts.FLAG_NOTICES:
                seen.add(name)
                notices.append(prompts.FLAG_NOTICES[name][language])
    return notices


def _context(
    docs: list[Document], language: str, confident: bool, focus: dict | None = None
) -> str:
    blocks = [f"[{d.metadata.get('title', 'Cruz Roja')}]\n{d.page_content}" for d in docs]
    notices = _notices(docs, language)
    if focus:
        notices.append(prompts.FOCUS_NOTICE[language].format(
            course=focus["name_es"] if language == "es" else focus["name_en"],
            course_id=focus["course_id"],
        ))
    if not confident:
        notices.append(prompts.NO_CONTEXT[language])
    return "\n\n".join(blocks + notices)


def _build_chain(language: str):
    template = ChatPromptTemplate.from_messages(
        [("system", prompts.SYSTEM[language]), ("human", prompts.HUMAN)]
    )
    return template | chat_model() | StrOutputParser()


def _verified_prices(docs: list[Document]) -> set[str]:
    """Every amount the retrieved courses are allowed to mention."""
    allowed: set[str] = set()
    for doc in docs:
        course = catalog.get_course(doc.metadata.get("course_id") or "")
        if not course:
            continue
        if course.get("price_mxn") is not None:
            allowed.add(f"{int(course['price_mxn'])}")
        for found in MONEY_RE.finditer(course.get("price_display") or ""):
            allowed.add(found.group(1).replace(",", "").rstrip("."))
    return allowed


def verify_prices(text: str, docs: list[Document]) -> bool:
    """True when every amount in the answer traces back to the catalogue.

    A wrong course recommendation is recoverable; a wrong *price* is a
    commercial problem for the client, so any amount we cannot trace means the
    generated text is discarded in favour of the stored facts.
    """
    allowed = _verified_prices(docs)
    for found in MONEY_RE.finditer(text):
        amount = found.group(1).replace(",", "").rstrip(".")
        if amount not in allowed:
            log.warning("Unverified amount $%s in generated answer — falling back", amount)
            return False
    return True


def _fallback(docs: list[Document], language: str) -> str:
    """Deterministic reply built straight from the catalogue row."""
    for doc in docs:
        course = catalog.get_course(doc.metadata.get("course_id") or "")
        if course:
            if language == "es":
                return (
                    f"*{course['name_es']}*\n"
                    f"• Duración: {course['contact_hours']} horas ({course['calendar_span']})\n"
                    f"• Precio: {course['price_display']}\n"
                    f"• Requisitos: {course['prerequisites']}\n"
                    f"• Al finalizar: {course['credential']}\n\n"
                    "¿Quieres que el equipo te confirme fechas disponibles?"
                )
            return (
                f"*{course['name_en']}*\n"
                f"• Duration: {course['contact_hours']} hours ({course['calendar_span']})\n"
                f"• Price: {course['price_display']}\n"
                f"• Requirements: {course['prerequisites']}\n"
                f"• You receive: {course['credential']}\n\n"
                "Would you like the team to confirm available dates?"
            )
    return (
        "Puedo consultarlo con el equipo de Cruz Roja para darte el dato exacto. "
        "¿Te contactamos?"
        if language == "es"
        else "I can check that with the Cruz Roja team so you get the exact detail. "
             "Shall we contact you?"
    )


def answer(
    question: str,
    history: list[dict] | None = None,
    language: str = "es",
    focus_course_id: str | None = None,
) -> Answer:
    """`focus_course_id` pins the conversation to one course.

    Someone halfway through signing up for BLS who asks "does it include the
    manual?" means *that* course. Without the pin, retrieval only sees the
    words in the question and answers about the catalogue in general.
    """
    question = question.strip()[: settings.max_user_chars]
    docs = retrieval.retrieve(question)

    focused = catalog.get_course(focus_course_id) if focus_course_id else None
    if focused and focus_course_id not in [d.metadata.get("course_id") for d in docs]:
        docs.insert(0, Document(
            page_content=retrieval.course_summary(focused),
            metadata={"course_id": focus_course_id, "title": focused["name_es"],
                      "score": 1.0, "via": "focus"},
        ))

    top_score = retrieval.best_score(docs)
    confident = top_score >= retrieval.CONFIDENCE_FLOOR

    text = _build_chain(language).invoke({
        "context": _context(docs, language, confident, focused),
        "history": _history_text(history or [], language),
        "question": question,
    }).strip()

    if not text or not verify_prices(text, docs):
        text = _fallback(docs, language)

    return Answer(
        text=text,
        course_ids=[d.metadata["course_id"] for d in docs if d.metadata.get("course_id")],
        confident=confident,
        top_score=top_score,
    )
