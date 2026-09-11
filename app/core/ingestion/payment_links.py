"""The store link that sells each course.

Cruz Roja takes payment on its own Shopify store, one product page per course.
The mapping lives in `data/course_payment_links.csv` rather than in the course
sheet itself, because the two are maintained by different people and on
different clocks: the catalogue changes when a course changes, the store links
change when a product is relisted.

Not every course has one. Twenty of the sixty-seven — the company brigades, the
long diplomas, the courses quoted per group — are not sold online at all, and
for those the agent must keep doing what it did before: take the lead and let
the team call. A missing link is therefore normal, never an error.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path

log = logging.getLogger(__name__)

LINKS_CSV = Path(__file__).resolve().parents[3] / "data" / "course_payment_links.csv"


def load(path: Path = LINKS_CSV) -> dict[str, str]:
    """course_id -> payment URL, for the courses the store actually sells.

    Rows with no Course_ID are store products with no course behind them
    (certificate reprints, instalments, the internal test product); they stay in
    the file for the client to reconcile against, and are ignored here.
    """
    if not path.exists():
        log.warning("%s not found — enrolling will fall back to a callback", path)
        return {}

    links: dict[str, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            course_id = (row.get("Course_ID") or "").strip()
            url = (row.get("Payment_Link") or "").strip()
            # https only: WhatsApp rejects a call-to-action button on any other
            # scheme, and a rejected message is a dead end mid-enrolment.
            if course_id and url.startswith("https://"):
                links[course_id] = url
    return links
