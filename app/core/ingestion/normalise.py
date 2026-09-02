"""Turns a raw catalogue row into the clean shape the rest of the app uses.

The source sheet is human-typed: prices appear as `755`, `$1,100`, and
`Inscripción 2000 + mensualidades (consultar)` on different rows; course names
run to 66 characters while a WhatsApp list row allows 24. Everything that has
to be decided about a value is decided exactly once, here.

Rule inherited from the client's data guide: we never *calculate* a price. The
numeric column is kept for lookups, but what the customer sees is
`price_display`, built from what the client wrote.
"""
from __future__ import annotations

import re

ACRONYM_RE = re.compile(r"\(([A-Z][A-Z0-9\-/]{1,9})\)")
PARENS_RE = re.compile(r"\([^)]*\)")
DIGITS_RE = re.compile(r"\d[\d,]*")

WHATSAPP_ROW_TITLE_MAX = 24

# GP009 carries a General Public prefix but is a 920-hour paid diploma with real
# entry requirements — the data guide calls this out as the one exception.
DIPLOMA_IDS = {"GP009", "HP041", "HP042", "HP043", "HP044"}
# RQI is delivered as an AHA programme, so it belongs with the international ones.
INTERNATIONAL_IDS = {"HP045"}


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def short_label(name: str) -> str:
    """<=24 characters, because that is all a WhatsApp list row will render.

    Prefers the course's own acronym (BLS, PHTLS, DAMP-B) — that is what these
    courses are actually called — and otherwise trims on a word boundary.
    """
    name = _clean(name)
    acronym = ACRONYM_RE.search(name)
    if acronym and len(name) > WHATSAPP_ROW_TITLE_MAX:
        return acronym.group(1)

    base = _clean(PARENS_RE.sub("", name)).strip(" -–—,")
    if len(base) <= WHATSAPP_ROW_TITLE_MAX:
        return base

    out = ""
    for word in base.split():
        if len(out) + len(word) + 1 > WHATSAPP_ROW_TITLE_MAX - 1:
            break
        out = f"{out} {word}".strip()
    return (out or base[: WHATSAPP_ROW_TITLE_MAX - 1]) + "…"


def menu_group(row: dict) -> str:
    """Which browse menu the course belongs under."""
    course_id = row["Course_ID"]
    track = row.get("Program_Track", "")
    category = row.get("Course_Category", "")

    if course_id in DIPLOMA_IDS or "Diplomado" in track:
        return "diploma"
    if course_id in INTERNATIONAL_IDS or "Certificación Internacional" in track:
        return "international"
    if category == "Certification" or "Certificación de profesionales" in track:
        return "certification"
    if category == "Instructor":
        return "instructor"
    if category == "Rescue Professionals":
        return "rescue"
    if category == "General Public (Employees)":
        return "employees"
    if category == "General Public":
        return "public"
    return "health"


def _money(raw: str) -> str:
    """`2000` -> `$2,000 MXN`."""
    return f"${int(raw.replace(',', '')):,} MXN"


def parse_price(raw: str) -> tuple[float | None, str]:
    """Returns (numeric price or None, text that is always safe to quote).

    Package-priced and monthly-instalment courses deliberately return None:
    there is no single number to quote, so the agent routes to the team.
    """
    raw = _clean(raw)
    if not raw:
        return None, "Consulta el precio con el equipo de Cruz Roja"

    if raw.replace(",", "").replace("$", "").isdigit():
        amount = float(raw.replace(",", "").replace("$", ""))
        return amount, f"{_money(raw)} por persona"

    lowered = raw.lower()
    if "inscripción" in lowered:
        found = DIGITS_RE.search(raw)
        enrolment = _money(found.group(0)) if found else "consultar"
        return None, f"Inscripción {enrolment} + mensualidades (consulta el monto con el equipo)"
    if "paquete" in lowered:
        return None, "Precio por paquete — el equipo te comparte las opciones disponibles"
    return None, raw


def normalise_course(row: dict) -> dict:
    """Raw CSV row -> the record stored in `courses` and rendered on WhatsApp."""
    name_es = _clean(row["Course_Name_ES"])
    price_mxn, price_display = parse_price(row.get("Price_Per_Person_MXN", ""))

    return {
        "course_id": _clean(row["Course_ID"]),
        "name_es": name_es,
        "name_en": _clean(row["Course_Name_EN"]),
        "short_label": short_label(name_es),
        "program_track": _clean(row.get("Program_Track")),
        "category": _clean(row.get("Course_Category")),
        "menu_group": menu_group(row),
        "short_description": _clean(row.get("Short_Description")),
        "target_audience": _clean(row.get("Target_Audience")),
        "prerequisites": _clean(row.get("Prerequisites_Description")),
        "minimum_age": _clean(row.get("Minimum_Age")),
        "required_education": _clean(row.get("Required_Education_Level")),
        "contact_hours": _clean(row.get("Total_Contact_Hours")),
        "duration_days": _clean(row.get("Duration_Days")),
        "calendar_span": _clean(row.get("Duration_Calendar_Span")),
        "schedule_format": _clean(row.get("Schedule_Format")),
        "delivery_mode": _clean(row.get("Delivery_Mode")),
        "language": _clean(row.get("Language_of_Instruction")),
        "materials": _clean(row.get("Materials_Included")),
        "assessment": _clean(row.get("Assessment_Method")),
        "passing_score": _clean(row.get("Passing_Score")),
        "max_participants": _clean(row.get("Max_Participants_Per_Class")),
        "min_participants": _clean(row.get("Min_Participants_To_Run")),
        "credential": _clean(row.get("What_They_Get")),
        "price_mxn": price_mxn,
        "price_display": price_display,
        "compliance_flags": _clean(row.get("Compliance_Flags")),
    }
