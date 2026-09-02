"""Finding the right card for a question.

Two searches run and their results are merged:

  * vector search — good at meaning. "I want to learn what to do if someone is
    choking" finds the CPR and airway-obstruction course without sharing a word
    with it.
  * name search — good at exact things. Course codes, acronyms and names where
    meaning-based search goes fuzzy ("PHTLS" is close to every other trauma card).

Each catches what the other misses, which is why both are here.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from app.config import settings
from app.db import catalog
from app.db import client as db
from app.providers.embeddings import embeddings

log = logging.getLogger(__name__)

# Below this cosine similarity we treat the catalogue as having no answer, log
# the question for Cruz Roja to review, and say so instead of guessing.
CONFIDENCE_FLOOR = 0.55
NAME_MATCH_SCORE = 0.95  # an exact-ish name hit outranks a fuzzy semantic one


class CatalogRetriever(BaseRetriever):
    """LangChain retriever over the pgvector card store."""

    k: int = settings.retrieval_k
    menu_group: str | None = None

    def _vector_search(self, query: str) -> list[Document]:
        vector = embeddings.embed_query(query)
        rows = db.query(
            "SELECT course_id, title, content, category, metadata, similarity "
            "FROM match_course_chunks(%s::vector, %s, %s)",
            (str(vector), self.k, self.menu_group),
        )
        return [
            Document(
                page_content=r["content"],
                metadata={**(r["metadata"] or {}), "course_id": r["course_id"],
                          "title": r["title"], "score": float(r["similarity"]), "via": "vector"},
            )
            for r in rows
        ]

    def _name_search(self, query: str) -> list[Document]:
        try:
            rows = catalog.search_by_name(query, limit=3)
        except Exception as exc:  # a missing extension must not take retrieval down
            log.warning("Name search unavailable: %r", exc)
            return []
        return [
            Document(
                page_content=course_summary(r),
                metadata={"course_id": r["course_id"], "title": r["name_es"],
                          "score": NAME_MATCH_SCORE, "via": "name"},
            )
            for r in rows
        ]

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun | None = None
    ) -> list[Document]:
        merged: dict[str, Document] = {}
        for doc in self._name_search(query) + self._vector_search(query):
            key = doc.metadata.get("course_id") or doc.metadata.get("title", "")
            existing = merged.get(key)
            if existing is None or doc.metadata["score"] > existing.metadata["score"]:
                merged[key] = doc
        ranked = sorted(merged.values(), key=lambda d: d.metadata["score"], reverse=True)
        return ranked[: self.k]


def course_summary(course: dict[str, Any]) -> str:
    """Plain-text card used when a course is found by name rather than by vector."""
    return (
        f"{course['name_es']} ({course['name_en']}) — clave {course['course_id']}.\n"
        f"{course.get('short_description', '')}\n"
        f"Dirigido a: {course.get('target_audience', '')}. "
        f"Requisitos: {course.get('prerequisites', '')}. Edad mínima: {course.get('minimum_age', '')}.\n"
        f"Duración: {course.get('contact_hours', '')} horas, {course.get('calendar_span', '')}, "
        f"{course.get('schedule_format', '')}. Modalidad: {course.get('delivery_mode', '')}. "
        f"Idioma: {course.get('language', '')}.\n"
        f"Incluye: {course.get('materials', '')}. "
        f"Evaluación: {course.get('assessment', '')} ({course.get('passing_score', '')}).\n"
        f"Cupo máximo: {course.get('max_participants', '')}. "
        f"Mínimo para abrir grupo: {course.get('min_participants', '')}.\n"
        f"Acredita: {course.get('credential', '')}. Precio: {course.get('price_display', '')}."
    )


retriever = CatalogRetriever()


def retrieve(query: str, k: int | None = None) -> list[Document]:
    return CatalogRetriever(k=k or settings.retrieval_k).invoke(query)


def best_score(docs: list[Document]) -> float:
    return max((d.metadata.get("score", 0.0) for d in docs), default=0.0)


def log_unanswered(phone: str, question: str, score: float, language: str) -> None:
    """Feeds the dashboard's 'unanswered' list so gaps get fixed at the source."""
    try:
        db.execute(
            "INSERT INTO unanswered_questions (phone, question, top_score, language) "
            "VALUES (%s, %s, %s, %s)",
            (phone, question[: settings.max_user_chars], score, language),
        )
    except Exception as exc:
        log.warning("Could not record unanswered question: %r", exc)
