"""Cruz Roja WhatsApp Agent Backend Service."""
import os
import json
import threading
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

# Optional: push completed enrollments to the Cruz Roja Dashboard. Skipped if unset.
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "").rstrip("/")
INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")

TRANSLATE_TRIGGER = "translate"
ENROLL_KEYWORDS = ("enroll", "enrol", "sign up", "signup", "register", "inscrib", "registrar")
CANCEL_KEYWORDS = ("cancel", "cancelar", "stop", "nevermind", "never mind")

# Broad "show me everything" intent — served from a deterministic category listing
# instead of the top-5 vector search, so nothing gets left out of the answer.
ALL_COURSES_KEYWORDS = (
    "all course", "all the course", "every course", "all your course",
    "full list", "complete list", "entire catalog", "full catalog", "whole catalog", "catalog",
    "todos los cursos", "todo el catálogo", "todo el catalogo", "catálogo completo", "catalogo completo",
    "lista completa", "lista de cursos",
)

# Heuristic for "this message is a question, not an answer to the current enrollment
# field" — lets a user ask something mid-flow without derailing the saved draft.
QUESTION_STARTERS = (
    "what", "how", "when", "where", "why", "which", "who", "can ", "could ", "do you",
    "does ", "is there", "are there", "will ", "should ", "would ",
    "qué", "que ", "cómo", "como ", "cuándo", "cuando ", "dónde", "donde ", "por qué", "porque ",
    "cuál", "cual ", "cuánto", "cuanto ", "quién", "quien ", "puedo", "puede", "hay ",
)


def looks_like_question(lower_text: str) -> bool:
    if "?" in lower_text:
        return True
    return lower_text.startswith(QUESTION_STARTERS)


ENROLL_STATE_FIELD = {
    "ENROLL_COURSE": "course",
    "ENROLL_NAME": "name",
    "ENROLL_EMAIL": "email",
    "ENROLL_PHONE": "phone",
    "ENROLL_ADDRESS": "address",
}


def current_enroll_prompt(state: str, language: str, draft: dict) -> str:
    field = ENROLL_STATE_FIELD[state]
    prompt = ENROLL_PROMPTS[language][field]
    if field == "email":
        prompt = prompt.format(name=draft.get("name", ""))
    return prompt

# Conversation states
GREET, CHATTING = "GREET", "CHATTING"
ENROLL_COURSE, ENROLL_NAME, ENROLL_EMAIL, ENROLL_PHONE, ENROLL_ADDRESS = (
    "ENROLL_COURSE", "ENROLL_NAME", "ENROLL_EMAIL", "ENROLL_PHONE", "ENROLL_ADDRESS"
)

app = FastAPI(title="Cruz Roja WhatsApp Agent")

# Serializes processing per phone number. Each process_message() call does several
# blocking DB round-trips and an HTTP send; without this, messages sent close
# together (e.g. a user quickly answering the enrollment prompts) can have their
# background tasks overlap and interleave reads/writes of the same lead row,
# corrupting conversation state. Single-worker deployment (WEB_CONCURRENCY=1 on
# Render) makes a plain in-process lock sufficient here.
_phone_locks: dict[str, threading.Lock] = {}
_phone_locks_guard = threading.Lock()


def _get_phone_lock(phone: str) -> threading.Lock:
    with _phone_locks_guard:
        lock = _phone_locks.get(phone)
        if lock is None:
            lock = threading.Lock()
            _phone_locks[phone] = lock
        return lock


def has_any(text: str, keywords: tuple) -> bool:
    return any(k in text for k in keywords)


# Mirrors the Dashboard's lib/validation.ts toLocalPhone() exactly, so a number
# accepted here is guaranteed to pass the dashboard's own isValidPhone() check
# and never gets silently rejected by its /api/public/leads endpoint.
DASHBOARD_PHONE_COUNTRY_CODE = "52"


def to_local_phone(value: str) -> str:
    digits = "".join(c for c in value if c.isdigit())
    cc = DASHBOARD_PHONE_COUNTRY_CODE
    if cc and len(digits) == 10 + len(cc) and digits.startswith(cc):
        return digits[len(cc):]
    if len(digits) == 11 and digits.startswith("0"):
        return digits[1:]
    return digits


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
        contact_name = None
        try:
            contact_name = entry["contacts"][0]["profile"]["name"]
        except (KeyError, IndexError):
            pass

        if msg.get("type") == "text":
            bg.add_task(process_message, msg["from"], msg["text"]["body"], contact_name, None)
        elif msg.get("type") == "interactive":
            interactive = msg.get("interactive", {})
            reply = interactive.get("button_reply") or interactive.get("list_reply")
            if reply:
                bg.add_task(process_message, msg["from"], reply.get("title", ""), contact_name, reply.get("id"))
    except (KeyError, IndexError):
        pass  # Event is a status update or unsupported message type; ignore

    return {"status": "ok"}  # Fast 200 response to satisfy Meta timeout


# 3. Conversation Pipeline (greeting -> chat/RAG -> enrollment -> dashboard)
def process_message(from_phone: str, user_text: str, contact_name: str | None = None, button_id: str | None = None):
    """Entry point for background tasks — serializes per phone number (see _get_phone_lock).

    Any unhandled error here (Gemini/Meta/DB transient failures) would otherwise abort
    silently, leaving the user with no reply and no idea their message didn't go through.
    """
    with _get_phone_lock(from_phone):
        try:
            _process_message_locked(from_phone, user_text, contact_name, button_id)
        except Exception as e:
            print(f"[ERROR] process_message crashed for {from_phone}: {e!r}")
            language = "en"
            try:
                language = (get_or_create_lead(from_phone)[0].get("language")) or "en"
            except Exception:
                pass
            send_whatsapp_message(from_phone, RETRY_MESSAGE.get(language, RETRY_MESSAGE["en"]))


def _process_message_locked(from_phone: str, user_text: str, contact_name: str | None, button_id: str | None):
    lead, is_new = get_or_create_lead(from_phone)
    language = lead.get("language") or "en"
    state = lead.get("state") or GREET
    draft = lead.get("enrollment_draft") or {}

    stripped = user_text.strip()
    lower = stripped.lower()

    # Greet brand-new leads once, then fall through to handle their actual message normally.
    if is_new:
        send_whatsapp_buttons(from_phone, GREETING_TEXT[language], GREETING_BUTTONS[language])
        set_lead_state(from_phone, CHATTING)
        state = CHATTING

    # Language switch works from any state
    if lower == TRANSLATE_TRIGGER:
        new_language = "es" if language == "en" else "en"
        set_lead_language(from_phone, new_language)
        send_whatsapp_message(from_phone, TRANSLATE_CONFIRMATION[new_language])
        return

    # Button taps carry unambiguous intent — handle before anything else
    if button_id == "enroll_now":
        set_enrollment_draft(from_phone, {})
        set_lead_state(from_phone, ENROLL_COURSE)
        send_whatsapp_message(from_phone, ENROLL_PROMPTS[language]["course"])
        return
    if button_id in ("browse_courses", "ask_question"):
        set_lead_state(from_phone, CHATTING)
        send_whatsapp_message(from_phone, BROWSE_HINT[language])
        return

    # Escape hatch out of an in-progress enrollment
    if state.startswith("ENROLL_") and has_any(lower, CANCEL_KEYWORDS):
        set_lead_state(from_phone, CHATTING)
        set_enrollment_draft(from_phone, {})
        send_whatsapp_message(from_phone, CANCEL_CONFIRMATION[language])
        return

    # Mid-enrollment question: answer it, then resume the same step — the draft
    # and state are untouched, so no progress collected so far is lost.
    if state.startswith("ENROLL_") and looks_like_question(lower):
        history = lead.get("history") or []
        if has_any(lower, ALL_COURSES_KEYWORDS):
            answer = list_all_courses(language)
            history.extend([
                {"role": "user", "text": user_text},
                {"role": "assistant", "text": ALL_COURSES_HISTORY_NOTE[language]},
            ])
        else:
            answer = rag_answer(user_text, history, language)
            history.extend([
                {"role": "user", "text": user_text},
                {"role": "assistant", "text": answer},
            ])
        save_lead_history(from_phone, history[-20:])
        send_whatsapp_message(
            from_phone,
            f"{answer}\n\n{RESUME_NOTE[language]}\n{current_enroll_prompt(state, language, draft)}",
        )
        return

    # --- Enrollment state machine ---
    if state == ENROLL_COURSE:
        draft["course"] = stripped
        set_enrollment_draft(from_phone, draft)
        set_lead_state(from_phone, ENROLL_NAME)
        send_whatsapp_message(from_phone, ENROLL_PROMPTS[language]["name"])
        return

    if state == ENROLL_NAME:
        draft["name"] = stripped
        set_enrollment_draft(from_phone, draft)
        set_lead_state(from_phone, ENROLL_EMAIL)
        send_whatsapp_message(from_phone, ENROLL_PROMPTS[language]["email"].format(name=draft["name"]))
        return

    if state == ENROLL_EMAIL:
        if "@" not in stripped or "." not in stripped:
            send_whatsapp_message(from_phone, ENROLL_PROMPTS[language]["email_retry"])
            return
        draft["email"] = stripped
        set_enrollment_draft(from_phone, draft)
        set_lead_state(from_phone, ENROLL_PHONE)
        send_whatsapp_message(from_phone, ENROLL_PROMPTS[language]["phone"])
        return

    if state == ENROLL_PHONE:
        if len(to_local_phone(stripped)) != 10:
            send_whatsapp_message(from_phone, ENROLL_PROMPTS[language]["phone_retry"])
            return
        draft["phone"] = stripped
        set_enrollment_draft(from_phone, draft)
        set_lead_state(from_phone, ENROLL_ADDRESS)
        send_whatsapp_message(from_phone, ENROLL_PROMPTS[language]["address"])
        return

    if state == ENROLL_ADDRESS:
        draft["address"] = stripped
        # Transition state before pushing so a duplicate Meta delivery can't double-submit.
        set_lead_state(from_phone, CHATTING)
        set_enrollment_draft(from_phone, {})
        push_enrollment_to_dashboard(from_phone, draft, language)
        send_whatsapp_message(
            from_phone,
            ENROLL_PROMPTS[language]["complete"].format(
                name=draft.get("name", ""), course=draft.get("course", ""),
                email=draft.get("email", ""), phone=draft.get("phone", ""),
            ),
        )
        return

    # --- CHATTING: normal Q&A, watching for enrollment intent ---
    if has_any(lower, ENROLL_KEYWORDS):
        set_enrollment_draft(from_phone, {})
        set_lead_state(from_phone, ENROLL_COURSE)
        send_whatsapp_message(from_phone, ENROLL_PROMPTS[language]["course"])
        return

    history = lead.get("history") or []

    # Broad "show me everything" ask: answer from a full, deterministic catalog
    # listing instead of the top-5 similarity search, so nothing is left out.
    if has_any(lower, ALL_COURSES_KEYWORDS):
        answer = list_all_courses(language)
        history.extend([
            {"role": "user", "text": user_text},
            {"role": "assistant", "text": ALL_COURSES_HISTORY_NOTE[language]},
        ])
        save_lead_history(from_phone, history[-20:])
        send_whatsapp_message(from_phone, answer)
        return

    reply = rag_answer(user_text, history, language)
    reply_with_hint = f"{reply}\n\n{CHAT_HINT[language]}"

    # Maintain conversation state (Keep up to 20 recent messages)
    history.extend([
        {"role": "user", "text": user_text},
        {"role": "assistant", "text": reply}
    ])
    save_lead_history(from_phone, history[-20:])
    send_whatsapp_message(from_phone, reply_with_hint)


# 4. Database Helper Functions
def get_or_create_lead(phone: str) -> tuple[dict, bool]:
    """Returns (lead, is_new). Atomic upsert — safe against Meta's duplicate webhook deliveries."""
    with psycopg.connect(DATABASE_URL, row_factory=dict_row, prepare_threshold=None) as conn:
        row = conn.execute(
            "INSERT INTO leads (phone) VALUES (%s) ON CONFLICT (phone) DO NOTHING RETURNING *",
            (phone,)
        ).fetchone()
        if row:
            conn.commit()
            return row, True
        row = conn.execute("SELECT * FROM leads WHERE phone = %s", (phone,)).fetchone()
        conn.commit()
        return row, False


def save_lead_history(phone: str, history: list):
    with psycopg.connect(DATABASE_URL, prepare_threshold=None) as conn:
        conn.execute(
            "UPDATE leads SET history = %s, last_seen_at = NOW() WHERE phone = %s",
            (json.dumps(history), phone)
        )
        conn.commit()


def set_lead_language(phone: str, language: str):
    with psycopg.connect(DATABASE_URL, prepare_threshold=None) as conn:
        conn.execute(
            "UPDATE leads SET language = %s, last_seen_at = NOW() WHERE phone = %s",
            (language, phone)
        )
        conn.commit()


def set_lead_state(phone: str, state: str):
    with psycopg.connect(DATABASE_URL, prepare_threshold=None) as conn:
        conn.execute(
            "UPDATE leads SET state = %s, last_seen_at = NOW() WHERE phone = %s",
            (state, phone)
        )
        conn.commit()


def set_enrollment_draft(phone: str, draft: dict):
    with psycopg.connect(DATABASE_URL, prepare_threshold=None) as conn:
        conn.execute(
            "UPDATE leads SET enrollment_draft = %s WHERE phone = %s",
            (json.dumps(draft), phone)
        )
        conn.commit()


def rag_answer(user_text: str, history: list, language: str) -> str:
    """Embed the query, pull the closest knowledge-base chunks, and generate a reply."""
    emb = gemini_client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=user_text,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=EMBEDDING_DIM,
        ),
    ).embeddings[0].values

    with psycopg.connect(DATABASE_URL, row_factory=dict_row, prepare_threshold=None) as conn:
        matches = conn.execute(
            "SELECT title, content, similarity FROM match_course_chunks(%s::vector, 5)",
            (str(emb),)
        ).fetchall()

    context = "\n\n".join(f"[{m['title']}]\n{m['content']}" for m in matches)
    return generate_llm_reply(user_text, context, history, language)


# Categories that aren't themselves course offerings, so they're left out of the
# "show me everything" listing.
NON_COURSE_CATEGORIES = ("Institutional", "Hospital Services", "Membership")
CATEGORY_LIST_LIMIT = 6


def list_all_courses(language: str) -> str:
    """Deterministic full-catalog listing, grouped by category — used for broad
    'what courses do you have' asks so the answer is guaranteed complete (no
    similarity-search truncation, no chance of the model dropping items)."""
    with psycopg.connect(DATABASE_URL, row_factory=dict_row, prepare_threshold=None) as conn:
        rows = conn.execute(
            "SELECT category, title FROM course_chunks "
            "WHERE category != ALL(%s) ORDER BY category, title",
            (list(NON_COURSE_CATEGORIES),)
        ).fetchall()

    grouped: dict[str, list[str]] = {}
    for r in rows:
        grouped.setdefault(r["category"], []).append(r["title"])

    lines = [ALL_COURSES_INTRO[language]]
    for category, titles in grouped.items():
        lines.append(f"\n*{category}* ({len(titles)})")
        shown = titles[:CATEGORY_LIST_LIMIT]
        lines.extend(f"• {t}" for t in shown)
        remaining = len(titles) - len(shown)
        if remaining > 0:
            lines.append(MORE_COURSES_LINE[language].format(n=remaining))
    lines.append(f"\n{ALL_COURSES_OUTRO[language]}")
    return "\n".join(lines)


def mark_lead_pushed(phone: str, error: str | None = None):
    with psycopg.connect(DATABASE_URL, prepare_threshold=None) as conn:
        conn.execute(
            "UPDATE leads SET pushed_to_dashboard = TRUE, dashboard_push_error = %s WHERE phone = %s",
            (error, phone)
        )
        conn.commit()


# 5. Conversational Text (bilingual) — greeting, hints, enrollment prompts
RETRY_MESSAGE = {
    "en": "⚠️ Sorry, something went wrong on my end. Could you please send that again?",
    "es": "⚠️ Lo siento, ocurrió un error de mi parte. ¿Podrías enviarlo de nuevo?",
}
GREETING_TEXT = {
    "en": "👋 Welcome to the Mexican Red Cross Training Coordination! I can help you learn about our courses and get you enrolled. What would you like to do?",
    "es": "👋 ¡Bienvenido a la Coordinación de Capacitación de la Cruz Roja Mexicana! Puedo ayudarte a conocer nuestros cursos e inscribirte. ¿Qué te gustaría hacer?",
}
GREETING_BUTTONS = {
    "en": [("browse_courses", "Browse Courses"), ("enroll_now", "Enroll Now"), ("ask_question", "Ask a Question")],
    "es": [("browse_courses", "Ver Cursos"), ("enroll_now", "Inscribirme"), ("ask_question", "Preguntar")],
}
BROWSE_HINT = {
    "en": 'Great! Ask me things like "What first aid courses do you have?" or "How much does CPR cost?" 💬',
    "es": 'Perfecto! Pregúntame cosas como "¿Qué cursos de primeros auxilios tienen?" o "¿Cuánto cuesta el CPR?" 💬',
}
CHAT_HINT = {
    "en": "💡 Ask about another course, or type *enroll* to sign up for one.",
    "es": "💡 Pregunta sobre otro curso, o escribe *inscribirme* para registrarte en uno.",
}
TRANSLATE_CONFIRMATION = {
    "es": "Idioma cambiado a español. ¿En qué puedo ayudarte?",
    "en": "Language switched to English. How can I help you?",
}
CANCEL_CONFIRMATION = {
    "en": "No problem, enrollment cancelled. Ask me anything about our courses anytime! 💬",
    "es": "No hay problema, inscripción cancelada. ¡Pregúntame lo que quieras sobre nuestros cursos! 💬",
}
ALL_COURSES_INTRO = {
    "en": "📚 Here's our full course catalog, grouped by category:",
    "es": "📚 Aquí tienes nuestro catálogo completo de cursos, agrupado por categoría:",
}
ALL_COURSES_OUTRO = {
    "en": "Ask me about any specific course for more details (price, schedule, etc.), or type *enroll* to sign up! 💬",
    "es": "Pregúntame sobre algún curso específico para más detalles (precio, horario, etc.), o escribe *inscribirme* para registrarte. 💬",
}
MORE_COURSES_LINE = {
    "en": "…and {n} more",
    "es": "…y {n} más",
}
ALL_COURSES_HISTORY_NOTE = {
    "en": "[Sent the full course catalog, grouped by category.]",
    "es": "[Se envió el catálogo completo de cursos, agrupado por categoría.]",
}
RESUME_NOTE = {
    "en": "↩️ Now, back to your enrollment —",
    "es": "↩️ Ahora, sigamos con tu inscripción —",
}
ENROLL_PROMPTS = {
    "en": {
        "course": "Great, let's get you enrolled! 📝 Which course are you interested in?\n(Type *cancel* anytime to stop.)",
        "name": "Thanks! What's your full name?\n(Type *cancel* anytime to stop.)",
        "email": "Nice to meet you, {name}! What's your email address?\n(Type *cancel* anytime to stop.)",
        "email_retry": "Hmm, that doesn't look like a valid email. Could you try again?",
        "phone": "Got it. What's the 10-digit phone number to reach you at? (e.g. 5512345678)\n(Type *cancel* anytime to stop.)",
        "phone_retry": "That doesn't look like a valid 10-digit phone number. Could you try again? (e.g. 5512345678)",
        "address": "Almost done! What's your address?\n(Type *cancel* anytime to stop.)",
        "complete": (
            "🎉 Thank you, {name}! Your interest in *{course}* has been received. "
            "Our team will contact you soon at {email} / {phone}. "
            "Feel free to keep asking me questions anytime!"
        ),
    },
    "es": {
        "course": "¡Perfecto, vamos a inscribirte! 📝 ¿En qué curso estás interesado?\n(Escribe *cancelar* en cualquier momento para detener.)",
        "name": "¡Gracias! ¿Cuál es tu nombre completo?\n(Escribe *cancelar* en cualquier momento para detener.)",
        "email": "¡Mucho gusto, {name}! ¿Cuál es tu correo electrónico?\n(Escribe *cancelar* en cualquier momento para detener.)",
        "email_retry": "Ese correo no parece válido. ¿Podrías intentarlo de nuevo?",
        "phone": "Entendido. ¿Cuál es tu número de teléfono a 10 dígitos? (ej. 5512345678)\n(Escribe *cancelar* en cualquier momento para detener.)",
        "phone_retry": "Ese número no parece un teléfono válido de 10 dígitos. ¿Podrías intentarlo de nuevo? (ej. 5512345678)",
        "address": "¡Ya casi! ¿Cuál es tu dirección?\n(Escribe *cancelar* en cualquier momento para detener.)",
        "complete": (
            "🎉 ¡Gracias, {name}! Hemos recibido tu interés en *{course}*. "
            "Nuestro equipo te contactará pronto al {email} / {phone}. "
            "¡Puedes seguir haciéndome preguntas cuando quieras!"
        ),
    },
}

SYSTEM_PROMPTS = {
    "en": """You are the official digital assistant for the Mexican Red Cross Training Coordination, chatting over WhatsApp.
You ALWAYS respond in clear, warm, and polite English, formatted for WhatsApp: short paragraphs, or a short bullet list when enumerating multiple items.
Keep replies concise (usually 2 to 5 sentences) but prioritize actually answering the question over hitting a length target — never truncate a direct answer (like a price or a course name) just to stay short.

RULES:
- Base your answers strictly on the CONTEXT provided below. Do not make up prices, dates, or non-existent courses.
- Be conversational and engaged, not robotic: react naturally to what the user said, and where it's genuinely helpful, ask a brief follow-up question (e.g. their experience level, or which format they prefer) instead of just dumping facts.
- If the user wants to register or enroll in a course, let them know they can type *enroll* right here in the chat to sign up.
- If asked about something outside the catalog, share what information you do have and direct them to contact support.
- If the request is a real emergency, instruct them immediately to call 911.""",
    "es": """Eres el asistente digital oficial de la Coordinación de Capacitación de la Cruz Roja Mexicana, conversando por WhatsApp.
SIEMPRE respondes en español claro, cálido y cortés, con formato para WhatsApp: párrafos cortos, o una lista breve con viñetas cuando enumeres varios elementos.
Sé conciso (normalmente de 2 a 5 oraciones), pero prioriza responder realmente la pregunta por encima de cumplir un límite de longitud — nunca recortes una respuesta directa (como un precio o el nombre de un curso) solo por brevedad.

REGLAS:
- Basa tus respuestas estrictamente en el CONTEXTO proporcionado a continuación. No inventes precios, fechas ni cursos inexistentes.
- Sé conversacional y cercano, no robótico: reacciona de forma natural a lo que dice el usuario y, cuando sea realmente útil, haz una breve pregunta de seguimiento (p. ej. su nivel de experiencia, o qué modalidad prefiere) en lugar de solo enumerar datos.
- Si el usuario desea registrarse o inscribirse en un curso, indícale que puede escribir *inscribirme* aquí mismo en el chat para registrarse.
- Si te preguntan algo fuera del catálogo, comparte la información que tengas y dirígelo a contactar soporte.
- Si la solicitud es una emergencia real, indícale de inmediato que llame al 911.""",
}


def generate_llm_reply(user_msg: str, context: str, history: list, language: str = "en") -> str:
    system_prompt = SYSTEM_PROMPTS.get(language, SYSTEM_PROMPTS["en"])
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
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    else:
        response = client.models.generate_content(
            model=GEMINI_CHAT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=300,
            ),
        )
        return (response.text or "").strip()


# 6. Meta WhatsApp Message Delivery Helpers
def _post_to_meta(payload: dict, action: str):
    headers = {
        "Authorization": f"Bearer {META_TOKEN}",
        "Content-Type": "application/json"
    }
    response = requests.post(META_API, headers=headers, json=payload, timeout=10)
    if not response.ok:
        print(f"[ERROR] Failed to {action}: {response.status_code} {response.text}")


def send_whatsapp_message(to_phone: str, message_body: str):
    _post_to_meta({
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": message_body}
    }, "send WhatsApp message")


def send_whatsapp_buttons(to_phone: str, body_text: str, buttons: list[tuple[str, str]]):
    """buttons: list of (id, title) tuples, max 3, title <= 20 chars."""
    _post_to_meta({
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": bid, "title": title}}
                    for bid, title in buttons
                ]
            },
        },
    }, "send WhatsApp buttons")


# 7. Dashboard Lead Push (Cruz Roja Records Dashboard) — fired on enrollment completion
def push_enrollment_to_dashboard(phone: str, draft: dict, language: str):
    if not DASHBOARD_URL or not INGEST_TOKEN:
        # Record this so it's diagnosable via a DB query even without Render log access.
        mark_lead_pushed(phone, error="Not configured: DASHBOARD_URL or INGEST_TOKEN missing on this deployment")
        return

    comment = (
        f"Course interest: {draft.get('course', 'N/A')}. "
        f"Address: {draft.get('address', 'N/A')}. "
        f"Requested via WhatsApp enrollment flow. Preferred language: {language}."
    )[:2000]

    payload = {
        "name": draft.get("name") or "WhatsApp Lead",
        "email": draft.get("email", ""),
        "phone": draft.get("phone") or phone,
        "comment": comment,
    }
    error = None
    try:
        response = requests.post(
            f"{DASHBOARD_URL}/api/public/leads",
            headers={"x-ingest-token": INGEST_TOKEN, "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        if not response.ok:
            error = f"HTTP {response.status_code}: {response.text[:500]}"
            print(f"[ERROR] Failed to push enrollment to dashboard: {error}")
    except requests.RequestException as e:
        error = f"Request failed: {e}"
        print(f"[ERROR] Dashboard push request failed: {e}")
    finally:
        mark_lead_pushed(phone, error=error)
