"""Reads of the structured catalogue.

Menus and price checks come from here rather than from the vector store: a menu
must show every course in a category in a stable order, and a price must be the
number the client typed, not the number a model recalled.

The catalogue changes only when someone uploads a new sheet, so it is cached in
memory for a few minutes — that removes a database round-trip from every menu tap.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from app.db import client as db

_CACHE_TTL_SECONDS = 300
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()

MENU_GROUPS = (
    "public", "employees", "health", "rescue", "international",
    "diploma", "certification", "instructor",
)

_COURSE_FIELDS = """
    course_id, name_es, name_en, short_label, program_track, category, menu_group,
    short_description, target_audience, prerequisites, minimum_age, required_education,
    contact_hours, duration_days, calendar_span, schedule_format, delivery_mode,
    language, materials, assessment, passing_score, max_participants, min_participants,
    credential, price_mxn, price_display, compliance_flags, payment_url
"""


def _cached(key: str, loader):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]
    value = loader()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def courses_in_group(group: str) -> list[dict]:
    return _cached(
        f"group:{group}",
        lambda: db.query(
            f"SELECT {_COURSE_FIELDS} FROM courses WHERE menu_group = %s ORDER BY course_id",
            (group,),
        ),
    )


def group_counts() -> dict[str, int]:
    def load() -> dict[str, int]:
        rows = db.query("SELECT menu_group, COUNT(*) AS n FROM courses GROUP BY menu_group")
        return {r["menu_group"]: r["n"] for r in rows}

    return _cached("group_counts", load)


def total_courses() -> int:
    return sum(group_counts().values())


def get_course(course_id: str) -> dict | None:
    return _cached(
        f"course:{course_id}",
        lambda: db.query_one(
            f"SELECT {_COURSE_FIELDS} FROM courses WHERE course_id = %s", (course_id,)
        ),
    )


def get_courses(course_ids: list[str]) -> dict[str, dict]:
    if not course_ids:
        return {}
    rows = db.query(
        f"SELECT {_COURSE_FIELDS} FROM courses WHERE course_id = ANY(%s)", (course_ids,)
    )
    return {r["course_id"]: r for r in rows}


def minimum_age(course: dict) -> int:
    """The age written on the row, as a number. Values read '15', '18' or '18 años'."""
    digits = "".join(c for c in (course.get("minimum_age") or "") if c.isdigit())
    return int(digits[:2]) if digits else 18


def is_age_eligible(course: dict, age: int | None) -> bool:
    """Unknown age is treated as eligible — we never invent a reason to exclude."""
    return age is None or age >= minimum_age(course)


def eligible_in_group(group: str, age: int | None) -> list[dict]:
    return [c for c in courses_in_group(group) if is_age_eligible(c, age)]


def search_by_name(term: str, limit: int = 5) -> list[dict]:
    """Fuzzy name match — handles typos and accents ("primeros auxilos")."""
    term = term.strip()
    if len(term) < 3:
        return []
    return db.query(
        f"""
        SELECT {_COURSE_FIELDS},
               GREATEST(
                   similarity(unaccent_lower(name_es), unaccent_lower(%s)),
                   similarity(unaccent_lower(name_en), unaccent_lower(%s))
               ) AS score
        FROM courses
        WHERE unaccent_lower(name_es) %% unaccent_lower(%s)
           OR unaccent_lower(name_en) %% unaccent_lower(%s)
           OR unaccent_lower(name_es) LIKE '%%' || unaccent_lower(%s) || '%%'
           OR upper(course_id) = upper(%s)
        ORDER BY score DESC
        LIMIT %s
        """,
        (term, term, term, term, term, term, limit),
    )
