"""parse -> normalise -> build cards -> embed -> store.

Run with `python ingest.py`. Safe to re-run: the whole catalogue is replaced in
one transaction, so a failed run leaves the live agent on the previous data
rather than on a half-loaded table.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path

from langchain_core.documents import Document
from psycopg.types.json import Jsonb

from app.core.ingestion.build_cards import course_card, institutional_cards
from app.core.ingestion.normalise import normalise_course
from app.db import client as db
from app.providers.embeddings import embeddings

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
CATALOGUE_CSV = DATA_DIR / "juptr_rc_courses.csv"
LEGACY_JSON = DATA_DIR / "cruz_roja_courses_products_en.json"

EMBED_BATCH = 32

COURSE_COLUMNS = (
    "course_id", "name_es", "name_en", "short_label", "program_track", "category",
    "menu_group", "short_description", "target_audience", "prerequisites", "minimum_age",
    "required_education", "contact_hours", "duration_days", "calendar_span",
    "schedule_format", "delivery_mode", "language", "materials", "assessment",
    "passing_score", "max_participants", "min_participants", "credential",
    "price_mxn", "price_display", "compliance_flags",
)


def read_catalogue(path: Path = CATALOGUE_CSV) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Generate it with: python scripts/build_catalog_csv.py"
        )
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [normalise_course(row) for row in csv.DictReader(fh)]


def _embed_all(docs: list[Document]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(docs), EMBED_BATCH):
        batch = docs[start : start + EMBED_BATCH]
        vectors.extend(embeddings.embed_documents([d.page_content for d in batch]))
        log.info("Embedded %s/%s cards", len(vectors), len(docs))
    return vectors


def run() -> dict:
    courses = read_catalogue()
    docs = [course_card(c) for c in courses] + institutional_cards(LEGACY_JSON)
    log.info("Built %s course rows and %s cards", len(courses), len(docs))

    vectors = _embed_all(docs)

    with db.connection() as conn:
        with conn.transaction():
            conn.execute("DELETE FROM course_chunks")
            conn.execute("DELETE FROM courses")

            conn.cursor().executemany(
                f"INSERT INTO courses ({', '.join(COURSE_COLUMNS)}) "
                f"VALUES ({', '.join(['%s'] * len(COURSE_COLUMNS))})",
                [tuple(c[col] for col in COURSE_COLUMNS) for c in courses],
            )
            conn.cursor().executemany(
                "INSERT INTO course_chunks (course_id, title, content, category, metadata, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    (
                        doc.metadata.get("course_id"),
                        doc.metadata["title"],
                        doc.page_content,
                        doc.metadata["category"],
                        Jsonb(doc.metadata),
                        str(vector),
                    )
                    for doc, vector in zip(docs, vectors)
                ],
            )

    log.info("Ingest complete: %s courses, %s cards", len(courses), len(docs))
    return {"courses": len(courses), "cards": len(docs)}
