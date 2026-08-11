"""One-time ingestion script for the Cruz Roja course catalogue in English."""
import os
import sys
from dotenv import load_dotenv
import psycopg
from google import genai
from google.genai import types

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 768

# English translated catalogue chunks
CHUNKS = [
    ("Institutional", "About the Training Coordination",
     "The Training Coordination of the Mexican Red Cross trains professionals and "
     "citizens in pre-hospital care with quality and warmth. The courses are "
     "backed by the Ministry of Labor and Social Welfare (STPS) and CONOCER, "
     "guaranteeing quality, official validity, and technical relevance."),

    ("First Aid", "Basic First Aid",
     "Basic first aid course aimed at the general public; foundation of the "
     "external training catalog. Ideal for anyone who wants to learn how to "
     "respond to common emergencies."),

    ("Pre-hospital Care", "First Responder",
     "Training as a certified First Responder in pre-hospital care, with "
     "certification issued by the Mexican Red Cross. More advanced level than "
     "basic first aid."),

    ("First Aid", "Pediatric First Aid",
     "First aid focused on infants and children; aimed at parents, teachers, and "
     "caregivers. Covers emergencies specific to the child population."),

    ("First Aid", "Geriatric First Aid",
     "First aid focused on older adults and their most frequent emergencies. "
     "Recommended for caregivers of the elderly and family members."),

    ("Resuscitation", "CPR and AED",
     "Cardiopulmonary resuscitation and use of the automated external defibrillator (AED). "
     "Critical skills to save lives in cardiac arrests."),

    ("Corporate", "Corporate Brigades",
     "Training and capacity building of emergency brigades in the workplace, for "
     "companies and institutions. Complies with civil protection obligations at work."),

    ("Corporate", "Firefighting",
     "Fire prevention and basic firefighting techniques. Aimed at brigade members, "
     "company and institution personnel."),

    ("Contact", "How to enroll",
     "To enroll in the courses, contact the Training Coordination at the email "
     "cursos@cruzrojamexicana.org.mx or by phone at 72 2335 6016. You can also "
     "enroll online at https://capacitacion.cruzrojamexicana.org.mx/. "
     "Address: Luis Vives 200, Polanco, CDMX 11510."),

    ("Contact", "Additional courses",
     "In addition to the main catalog, there are additional specialized courses available "
     "upon request from the Training Coordination. Contact by email or phone "
     "for the complete catalog.")
]


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

    with psycopg.connect(db_url, prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM course_chunks;")
            for cat, title, content in CHUNKS:
                vec = embed(f"{title}. {content}")
                cur.execute(
                    "INSERT INTO course_chunks (category, title, content, embedding) "
                    "VALUES (%s, %s, %s, %s);",
                    (cat, title, content, str(vec))
                )
                print(f" ✓ Ingested: {title}")
        conn.commit()
    print(f"\nSuccessfully ingested {len(CHUNKS)} course chunks.")


if __name__ == "__main__":
    main()
