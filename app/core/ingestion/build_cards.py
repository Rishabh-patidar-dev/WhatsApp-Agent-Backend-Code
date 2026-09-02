"""Builds the retrievable cards.

One card per course: a single self-contained document holding everything about
that course in readable prose. Cards beat raw spreadsheet rows for retrieval
because a question like "what do I need to take the lifeguard course" matches
the sentence about prerequisites, not a bare cell value.

Course names are written in both languages inside the card so an English
question still lands on a Spanish course record.
"""
from __future__ import annotations

import json
from pathlib import Path

from langchain_core.documents import Document


def course_card(course: dict) -> Document:
    c = course
    lines = [
        f"{c['name_es']} ({c['name_en']}) — clave {c['course_id']}",
        f"Programa: {c['program_track']}. Categoría: {c['category']}.",
    ]
    if c["short_description"]:
        lines.append(f"Descripción: {c['short_description']}")
    if c["target_audience"]:
        lines.append(f"Dirigido a: {c['target_audience']}.")
    if c["prerequisites"]:
        lines.append(
            f"Requisitos de ingreso: {c['prerequisites']}. "
            f"Edad mínima: {c['minimum_age']}. Escolaridad: {c['required_education']}."
        )
    lines.append(
        f"Duración: {c['contact_hours']} horas de contacto, {c['calendar_span']}, "
        f"{c['schedule_format']}."
    )
    lines.append(f"Modalidad: {c['delivery_mode']}. Idioma: {c['language']}.")
    if c["materials"]:
        lines.append(f"Incluye: {c['materials']}.")
    lines.append(f"Evaluación: {c['assessment']}. Calificación mínima: {c['passing_score']}.")
    lines.append(
        f"Cupo máximo: {c['max_participants']}. Mínimo para abrir grupo: {c['min_participants']}."
    )
    lines.append(f"Al finalizar recibes: {c['credential']}.")
    lines.append(f"Precio: {c['price_display']}.")

    return Document(
        page_content="\n".join(lines),
        metadata={
            "course_id": c["course_id"],
            "title": c["name_es"],
            "category": c["category"],
            "menu_group": c["menu_group"],
            "kind": "course",
        },
    )


def institutional_cards(legacy_json: Path) -> list[Document]:
    """Contact details, membership and hospital services from the earlier dataset.

    The course catalogue is superseded by the client's sheet, but this file is
    still the only source for phone numbers, addresses and the membership product.
    """
    if not legacy_json.exists():
        return []

    data = json.loads(legacy_json.read_text(encoding="utf-8"))
    docs: list[Document] = []

    org = data.get("organization", {})
    if org:
        docs.append(Document(
            page_content=(
                f"{org.get('name', 'Cruz Roja Mexicana')} atiende {org.get('region', '')}. "
                f"La capacitación la coordina {org.get('training_division', '')}. "
                f"Teléfono de emergencias: {org.get('emergency_phone', '911')}. "
                f"Línea de urgencias: {org.get('emergency_urgent_care_phone', '')}. "
                f"Contacto de capacitación por WhatsApp: {org.get('training_contact_whatsapp', '')}. "
                f"Correo de tienda: {org.get('store_contact_email', '')}. "
                f"Teléfono de la escuela: {org.get('school_contact_phone', '')}. "
                f"Dirección: {org.get('address', '')}."
            ),
            metadata={"course_id": None, "title": "Contacto y datos institucionales",
                      "category": "Institucional", "menu_group": "contact", "kind": "institutional"},
        ))

    membership = data.get("membership_product", {})
    for plan in membership.get("plans", []):
        docs.append(Document(
            page_content=(
                f"{membership.get('name', 'Membresía')} — plan {plan.get('plan_name', '')}. "
                f"{membership.get('description', '')} "
                f"Precio: {plan.get('price', 'consultar')} ({plan.get('billing_unit', '')}). "
                f"Beneficios: {'; '.join(plan.get('benefits', []))}. "
                f"Más información: {membership.get('purchase_url', '')}."
            ),
            metadata={"course_id": None, "title": f"Membresía — {plan.get('plan_name', '')}",
                      "category": "Membresía", "menu_group": "membership", "kind": "membership"},
        ))

    hospital = data.get("hospital_services", {})
    if hospital:
        docs.append(Document(
            page_content=(
                f"Servicios hospitalarios y médicos de Cruz Roja. "
                f"Teléfono: {hospital.get('phone', '')}. "
                f"Servicios: {', '.join(hospital.get('services', []))}."
            ),
            metadata={"course_id": None, "title": "Servicios hospitalarios",
                      "category": "Servicios", "menu_group": "services", "kind": "institutional"},
        ))

    return docs
