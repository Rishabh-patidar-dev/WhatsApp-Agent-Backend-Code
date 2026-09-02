"""The tappable menu tree.

Everything the person can tap has a stable id, and every id is parsed back in
one place (`parse_action`). Ids look like:

    menu:main          the main menu
    menu:browse        the category list
    grp:health:9       a page of one category, starting at offset 9
    crs:HP038          one course card
    act:enroll         start the sign-up flow
    act:enroll:HP038   start it with the course already chosen

Menus never call the model: a tap is answered from the database, which is why
tapping is instant and costs nothing per message.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.channels.whatsapp import format as fmt
from app.core.copy import GROUP_DESCRIPTIONS, GROUP_LABELS, t
from app.db import catalog

COURSES_PER_PAGE = 9  # 10th row is reserved for "see more" / "main menu"


@dataclass
class Action:
    kind: str           # menu | group | course | act | unsupported | unknown
    value: str = ""
    offset: int = 0
    course_id: str = ""


def parse_action(reply_id: str | None) -> Action:
    if not reply_id:
        return Action("unknown")
    parts = reply_id.split(":")
    head = parts[0]

    if head == "menu":
        return Action("menu", parts[1] if len(parts) > 1 else "main")
    if head == "grp":
        offset = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        return Action("group", parts[1], offset=offset)
    if head == "crs":
        return Action("course", course_id=parts[1] if len(parts) > 1 else "")
    if head == "act":
        return Action("act", parts[1] if len(parts) > 1 else "",
                      course_id=parts[2] if len(parts) > 2 else "")
    if head == "unsupported":
        return Action("unsupported", parts[1] if len(parts) > 1 else "")
    return Action("unknown", reply_id)


# --- menu payload builders -------------------------------------------------
# Each returns the arguments for whatsapp.client.send_list().


def main_menu(language: str) -> dict:
    return {
        "header": t("menu_header", language),
        "body": t("menu_body", language),
        "footer": t("menu_footer", language),
        "button_label": t("menu_button", language),
        "sections": [
            {
                "title": t("section_courses", language),
                "rows": [
                    {"id": "menu:browse", "title": "📚 " + t("browse_header", language),
                     "description": t("browse_body", language, total=catalog.total_courses())},
                    {"id": "act:enroll", "title": "📝 " + t("btn_enroll", language),
                     "description": {"es": "Regístrate en un curso", "en": "Register for a course"}[language]},
                    {"id": "act:prices", "title": "💵 " + {"es": "Precios", "en": "Prices"}[language],
                     "description": {"es": "Costos y formas de pago", "en": "Costs and payment"}[language]},
                    {"id": "act:requirements", "title": "📋 " + {"es": "Requisitos", "en": "Requirements"}[language],
                     "description": {"es": "Edad, escolaridad y documentos", "en": "Age, education, documents"}[language]},
                ],
            },
            {
                "title": t("section_help", language),
                "rows": [
                    {"id": "act:faq", "title": "❓ " + {"es": "Preguntas frecuentes", "en": "FAQs"}[language],
                     "description": {"es": "Constancias, idiomas, grupos", "en": "Certificates, languages, groups"}[language]},
                    {"id": "act:contact", "title": "📞 " + {"es": "Hablar con el equipo", "en": "Talk to the team"}[language],
                     "description": {"es": "Te contactamos por teléfono o correo", "en": "We'll contact you by phone or email"}[language]},
                    {"id": "act:language", "title": "🌐 English / Español",
                     "description": {"es": "Cambiar idioma", "en": "Change language"}[language]},
                ],
            },
        ],
    }


def browse_menu(language: str) -> dict:
    counts = catalog.group_counts()
    rows = []
    for group in catalog.MENU_GROUPS:
        count = counts.get(group, 0)
        if not count:
            continue
        rows.append({
            "id": f"grp:{group}",
            "title": f"{GROUP_LABELS[group][language]} ({count})",
            "description": GROUP_DESCRIPTIONS[group][language],
        })
    rows.append({"id": "menu:main", "title": t("back_row", language),
                 "description": t("back_row_desc", language)})

    return {
        "header": t("browse_header", language),
        "body": t("browse_body", language, total=catalog.total_courses()),
        "footer": t("menu_footer", language),
        "button_label": t("browse_button", language),
        "sections": [{"title": t("section_categories", language), "rows": rows}],
    }


def _price_short(course: dict, language: str) -> str:
    if course.get("price_mxn") is not None:
        return f"${int(course['price_mxn']):,} MXN"
    return {"es": "Consultar precio", "en": "Ask for price"}[language]


def course_list(group: str, language: str, offset: int = 0) -> dict | None:
    courses = catalog.courses_in_group(group)
    if not courses:
        return None

    page = courses[offset : offset + COURSES_PER_PAGE]
    rows = [
        {
            "id": f"crs:{c['course_id']}",
            "title": c["short_label"],
            "description": f"{c['contact_hours']} h · {_price_short(c, language)}",
        }
        for c in page
    ]

    remaining = len(courses) - (offset + len(page))
    if remaining > 0:
        rows.append({
            "id": f"grp:{group}:{offset + COURSES_PER_PAGE}",
            "title": t("more_row", language),
            "description": t("more_row_desc", language, n=min(remaining, COURSES_PER_PAGE)),
        })
    else:
        rows.append({"id": "menu:browse", "title": t("categories_row", language),
                     "description": t("back_row_desc", language)})

    return {
        "header": GROUP_LABELS[group][language],
        "body": t("course_list_body", language, group=GROUP_LABELS[group][language],
                  count=len(courses), start=offset + 1, end=offset + len(page)),
        "footer": t("menu_footer", language),
        "button_label": t("course_list_button", language),
        "sections": [{"title": GROUP_LABELS[group][language], "rows": rows}],
    }


def course_detail(course: dict, language: str) -> str:
    name = course["name_es"] if language == "es" else course["name_en"]
    description = course.get("short_description") or ""
    return t(
        "course_detail", language,
        name=name,
        hours=course.get("contact_hours", "—"),
        span=course.get("calendar_span", "—"),
        schedule=course.get("schedule_format", "—"),
        delivery=course.get("delivery_mode", "—"),
        language=course.get("language", "—"),
        audience=course.get("target_audience", "—"),
        prerequisites=course.get("prerequisites", "—"),
        age=course.get("minimum_age", "—"),
        education=course.get("required_education", "—"),
        credential=course.get("credential", "—"),
        price=course.get("price_display", "—"),
        description=fmt.clip(description, 300),
    )


def course_buttons(course_id: str, language: str) -> list[tuple[str, str]]:
    return [
        (f"act:enroll:{course_id}", t("btn_enroll", language)),
        ("menu:browse", t("btn_other_courses", language)),
        ("menu:main", t("btn_menu", language)),
    ]
