"""Cruz Roja WhatsApp Agent Backend Service (English)."""
import os
import json
import requests
import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

# Configuration
META_TOKEN = os.environ["META_ACCESS_TOKEN"]
META_PHONE_ID = os.environ["META_PHONE_NUMBER_ID"]
VERIFY_TOKEN = os.environ["META_VERIFY_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]

META_API = f"https://graph.facebook.com/v20.0/{META_PHONE_ID}/messages"

# Gemini is used for embeddings unconditionally (free tier, no credit card needed)
gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 768
GEMINI_CHAT_MODEL = "gemini-3.5-flash-lite"

# Chat LLM Selection — defaults to free Gemini; set CHAT_PROVIDER=claude to use Anthropic instead
CHAT_PROVIDER = os.environ.get("CHAT_PROVIDER", "gemini").strip().lower()
if CHAT_PROVIDER == "claude":
    from anthropic import Anthropic
    LLM = ("claude", Anthropic())
else:
    LLM = ("gemini", gemini_client)

app = FastAPI(title="Cruz Roja WhatsApp Agent")


# 1. Meta Webhook Verification Endpoint
@app.get("/webhook")
def verify_webhook(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == VERIFY_TOKEN:
        return int(q.get("hub.challenge"))
    raise HTTPException(status_code=403, detail="Bad verification token")


# 2. Meta Webhook Incoming Message Receiver Endpoint
@app.post("/webhook")
async def receive_webhook(request: Request, bg: BackgroundTasks):
    payload = await request.json()
    try:
        entry = payload["entry"][0]["changes"][0]["value"]
        msg = entry["messages"][0]
        if msg.get("type") == "text":
            bg.add_task(process_message, msg["from"], msg["text"]["body"])
    except (KeyError, IndexError):
        pass  # Event is a status update or non-text message; ignore

    return {"status": "ok"}  # Fast 200 response to satisfy Meta timeout


# 3. RAG Core Pipeline
def process_message(from_phone: str, user_text: str):
    lead = get_or_create_lead(from_phone)
    history = lead.get("history") or []

    # Generate query embedding
    emb = gemini_client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=user_text,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=EMBEDDING_DIM,
        ),
    ).embeddings[0].values

    # Match relevant knowledge base chunks from Supabase
    with psycopg.connect(DATABASE_URL, row_factory=dict_row, prepare_threshold=None) as conn:
        matches = conn.execute(
            "SELECT title, content, similarity FROM match_course_chunks(%s::vector, 5)",
            (str(emb),)
        ).fetchall()

    context = "\n\n".join(f"[{m['title']}]\n{m['content']}" for m in matches)
    reply = generate_llm_reply(user_text, context, history)

    # Maintain conversation state (Keep up to 20 recent messages)
    history.extend([
        {"role": "user", "text": user_text},
        {"role": "assistant", "text": reply}
    ])
    save_lead_history(from_phone, history[-20:])
    send_whatsapp_message(from_phone, reply)


# 4. Database Helper Functions
def get_or_create_lead(phone: str) -> dict:
    with psycopg.connect(DATABASE_URL, row_factory=dict_row, prepare_threshold=None) as conn:
        row = conn.execute("SELECT * FROM leads WHERE phone = %s", (phone,)).fetchone()
        if row:
            return row
        conn.execute("INSERT INTO leads (phone) VALUES (%s)", (phone,))
        conn.commit()
        return {"phone": phone, "history": []}


def save_lead_history(phone: str, history: list):
    with psycopg.connect(DATABASE_URL, prepare_threshold=None) as conn:
        conn.execute(
            "UPDATE leads SET history = %s, last_seen_at = NOW() WHERE phone = %s",
            (json.dumps(history), phone)
        )
        conn.commit()


# 5. LLM Prompting & Response Generation
SYSTEM_PROMPT = """You are the official digital assistant for the Mexican Red Cross Training Coordination.
You ALWAYS respond in clear, helpful, and polite English, strictly within 2 to 4 sentences maximum (formatting for WhatsApp).

RULES:
- Base your answers strictly on the CONTEXT provided below. Do not make up prices, dates, or non-existent courses.
- If the user wants to register or enroll, direct them to email cursos@cruzrojamexicana.org.mx or call 72 2335 6016.
- If asked about something outside the catalog, share what information you do have and direct them to contact support.
- If the request is a real emergency, instruct them immediately to call 911."""


def generate_llm_reply(user_msg: str, context: str, history: list) -> str:
    hist_text = "\n".join(
        f"{'User' if h['role'] == 'user' else 'Assistant'}: {h['text']}"
        for h in history[-6:]
    )
    prompt = (
        f"CONTEXT (Official Red Cross Info):\n{context}\n\n"
        f"RECENT HISTORY:\n{hist_text}\n\n"
        f"NEW USER MESSAGE: {user_msg}\n\nReply:"
    )

    kind, client = LLM
    if kind == "claude":
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    else:
        response = client.models.generate_content(
            model=GEMINI_CHAT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                max_output_tokens=300,
            ),
        )
        return (response.text or "").strip()


# 6. Meta WhatsApp Message Delivery Helper
def send_whatsapp_message(to_phone: str, message_body: str):
    headers = {
        "Authorization": f"Bearer {META_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": message_body}
    }
    response = requests.post(META_API, headers=headers, json=payload, timeout=10)
    if not response.ok:
        print(f"[ERROR] Failed to send WhatsApp message: {response.status_code} {response.text}")
