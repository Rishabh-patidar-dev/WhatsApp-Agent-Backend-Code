"""Ingestion script for the Cruz Roja course & product catalogue.

Loads data/cruz_roja_courses_products_en.json, flattens it into RAG chunks,
embeds each with Gemini, and (re)populates the `course_chunks` table.
"""
import json
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from google import genai
from google.genai import types

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 768

DATA_FILE = Path(__file__).parent / "data" / "cruz_roja_courses_products_en.json"


def fmt_price(item: dict) -> str:
    parts = []
    if item.get("price"):
        parts.append(f"Price: {item['price']}")
    if item.get("list_price") and item.get("on_offer"):
        parts.append(f"(list price {item['list_price']}, on offer)")
    if "in_stock" in item:
        parts.append("In stock." if item["in_stock"] else "Currently sold out.")
    text = " ".join(parts)
    if text and not text.endswith("."):
        text += "."
    return text


def build_chunks(data: dict) -> list[tuple[str, str, str]]:
    """Return a list of (category, title, content) tuples."""
    chunks: list[tuple[str, str, str]] = []

    org = data["organization"]
    chunks.append((
        "Institutional",
        "About the Mexican Red Cross Training Coordination",
        f"{org['name']} serves {org['region']}. Training is coordinated by {org['training_division']}. "
        f"Emergency phone: {org['emergency_phone']}. Urgent care line: {org['emergency_urgent_care_phone']}. "
        f"Training contact via WhatsApp: {org['training_contact_whatsapp']}. Store contact: {org['store_contact_email']}. "
        f"School phone: {org['school_contact_phone']}. Address: {org['address']}."
    ))

    mem = data["membership_product"]
    for plan in mem["plans"]:
        benefits = "; ".join(plan.get("benefits", []))
        price_line = f" Price: {plan.get('price', 'contact for pricing')}."
        if plan.get("price_equivalent"):
            price_line += f" ({plan['price_equivalent']})"
        chunks.append((
            "Membership",
            f"{mem['name']} - {plan['plan_name']}",
            f"{mem['description']} Plan: {plan['plan_name']} ({plan.get('billing_unit', '')}).{price_line} "
            f"Benefits: {benefits}. Purchase/info: {mem['purchase_url']}."
        ))

    damp = data["paramedic_training_program"]
    for level in damp["levels"]:
        text = f"{damp['name']} — {level['level']}. Target audience: {level.get('target_audience', '')}."
        if level.get("enroll_url"):
            text += f" Enroll: {level['enroll_url']}."
        if level.get("info_url"):
            text += f" Info: {level['info_url']}."
        for pkg in level.get("packages", []):
            text += f" {pkg['package_name']} includes: {'; '.join(pkg['includes'])}."
        chunks.append(("Paramedic Training", f"{damp['name']} - {level['level']}", text))

    nursing = data["nursing_program"]
    locations = "; ".join(f"{c['location']}: {c['email']}" for c in nursing["contact_locations"])
    chunks.append((
        "Nursing Program",
        nursing["name"],
        f"{nursing['description']} Components: {'; '.join(nursing['components'])}. Contact locations: {locations}."
    ))

    for dip in data["diplomas"]:
        text = dip.get("objective", dip.get("description", ""))
        if dip.get("academic_endorsement"):
            text += f" Academically endorsed by {dip['academic_endorsement']}."
        text += f" Enrollment price: {dip.get('enrollment_price', 'contact for pricing')}."
        if dip.get("monthly_price"):
            text += f" Monthly price: {dip['monthly_price']}."
        text += f" Purchase: {dip['purchase_url']}."
        chunks.append(("Diploma", dip["name"], text))

    for cert in data["red_conocer_certifications"]:
        chunks.append((
            "RED CONOCER Certification",
            f"{cert['name']} ({cert['code']})",
            f"{cert['objective']} {fmt_price(cert)} Purchase: {cert['purchase_url']}."
        ))

    renewal = data["certificate_renewal_programs"]
    pts = renewal["points_based_renewal"]
    chunks.append((
        "Certificate Renewal",
        "Points-based certificate renewal",
        f"State level: {pts['state_level']['description']} Points required: basic "
        f"{pts['state_level']['points_required']['basic']}, advanced {pts['state_level']['points_required']['advanced']}. "
        f"National level: {pts['national_level']['description']} Points required: basic "
        f"{pts['national_level']['points_required']['basic']}, advanced {pts['national_level']['points_required']['advanced']}. "
        f"Certifications platform: {renewal['certifications_platform_url']}."
    ))
    for prog in renewal["state_update_program"]:
        chunks.append((
            "Certificate Renewal",
            f"State Update Program - {prog['level']}",
            f"Target audience: {prog['target_audience']}. Duration: {prog['duration']}. "
            f"Modality: {prog['modality']}. Start dates: {prog['start_dates']}."
        ))

    for course in data["courses"]:
        parts = []
        if course.get("provider"):
            parts.append(f"Provider: {course['provider']}.")
        if course.get("objective"):
            parts.append(course["objective"])
        elif course.get("description"):
            parts.append(course["description"])
        if course.get("affiliation_note"):
            parts.append(course["affiliation_note"])
        price_info = fmt_price(course)
        if price_info:
            parts.append(price_info)
        if course.get("purchase_url"):
            parts.append(f"Purchase: {course['purchase_url']}.")
        chunks.append((course["category"], course["name"], " ".join(parts)))

    hosp = data["hospital_services"]
    chunks.append((
        "Hospital Services",
        "Related Red Cross hospital & medical services",
        f"Phone: {hosp['phone']}. Services offered: {', '.join(hosp['services'])}."
    ))

    return chunks


def embed(text: str) -> list:
    r = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=EMBEDDING_DIM,
        ),
    )
    return r.embeddings[0].values


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL environment variable is not set.")

    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)

    chunks = build_chunks(data)
    print(f"Built {len(chunks)} chunks from {DATA_FILE.name}")

    with psycopg.connect(db_url, prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM course_chunks;")
            for cat, title, content in chunks:
                vec = embed(f"{title}. {content}")
                cur.execute(
                    "INSERT INTO course_chunks (category, title, content, embedding) "
                    "VALUES (%s, %s, %s, %s);",
                    (cat, title, content, str(vec))
                )
                print(f" ✓ Ingested: {title}")
        conn.commit()
    print(f"\nSuccessfully ingested {len(chunks)} course chunks.")


if __name__ == "__main__":
    main()
