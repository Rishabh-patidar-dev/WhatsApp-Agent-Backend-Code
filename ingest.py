"""Loads the course catalogue into the database.

    python ingest.py

Reads data/juptr_rc_courses.csv, builds one card per course, embeds them and
replaces the contents of `courses` and `course_chunks` in a single transaction.
"""
import logging

from app.config import settings
from app.core.ingestion.pipeline import run

if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(message)s")
    result = run()
    print(f"Ingested {result['courses']} courses as {result['cards']} cards.")
