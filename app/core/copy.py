"""Every line the agent can say, in both languages, in one file.

Kept apart from the logic so Cruz Roja can have the wording changed without
anyone touching the conversation flow.
"""
from __future__ import annotations

ES = "es"
EN = "en"

COPY: dict[str, dict[str, str]] = {
    "greeting_new": {
        "es": "¡Hola{name}! Bienvenido a la *Coordinación de Capacitación de la Cruz Roja Mexicana*.",
        "en": "Hello{name}! Welcome to the *Mexican Red Cross Training Coordination*.",
    },
    "greeting_back": {
        "es": "¡Qué gusto verte de nuevo{name}! ¿En qué te ayudo hoy?",
        "en": "Good to see you again{name}! How can I help today?",
    },
    "menu_header": {"es": "Menú principal", "en": "Main menu"},
    "menu_body": {
        "es": "¿Qué te gustaría hacer? Elige una opción del menú o escríbeme tu pregunta.",
        "en": "What would you like to do? Pick an option from the menu, or type your question.",
    },
    "menu_footer": {
        "es": "Escribe *menú* en cualquier momento",
        "en": "Type *menu* at any time",
    },
    "menu_button": {"es": "Ver opciones", "en": "View options"},
    "section_courses": {"es": "Cursos y programas", "en": "Courses & programmes"},
    "section_help": {"es": "Ayuda", "en": "Help"},

    "browse_header": {"es": "Catálogo de cursos", "en": "Course catalogue"},
    "browse_body": {
        "es": "Tenemos {total} cursos y programas. ¿Qué categoría te interesa?",
        "en": "We have {total} courses and programmes. Which category interests you?",
    },
    "browse_button": {"es": "Ver categorías", "en": "View categories"},
    "section_categories": {"es": "Categorías", "en": "Categories"},

    "course_list_body": {
        "es": "*{group}* — {count} cursos.\nMostrando {start}-{end}. Toca uno para ver el detalle completo.",
        "en": "*{group}* — {count} courses.\nShowing {start}-{end}. Tap one for the full details.",
    },
    "course_list_button": {"es": "Ver cursos", "en": "View courses"},
    "more_row": {"es": "Ver más cursos", "en": "See more courses"},
    "more_row_desc": {"es": "Siguientes {n} de esta categoría", "en": "Next {n} in this category"},
    "back_row": {"es": "Menú principal", "en": "Main menu"},
    "back_row_desc": {"es": "Volver al inicio", "en": "Back to the start"},
    "categories_row": {"es": "Otras categorías", "en": "Other categories"},

    "course_detail": {
        "es": "*{name}*\n"
              "{hours} horas · {span} · {schedule}\n"
              "{delivery} · {language}\n"
              "Dirigido a: {audience}\n"
              "Requisitos: {prerequisites}\n"
              "Edad mínima: {age} · Escolaridad: {education}\n"
              "Al finalizar recibes: {credential}\n"
              "{price}\n\n"
              "{description}",
        "en": "*{name}*\n"
              "{hours} hours · {span} · {schedule}\n"
              "{delivery} · {language}\n"
              "For: {audience}\n"
              "Requirements: {prerequisites}\n"
              "Minimum age: {age} · Education: {education}\n"
              "You receive: {credential}\n"
              "{price}\n\n"
              "{description}",
    },
    "course_actions": {
        "es": "¿Te inscribo en este curso o prefieres ver otros?",
        "en": "Shall I sign you up for this one, or would you rather see others?",
    },
    "btn_enroll": {"es": "Inscribirme", "en": "Sign me up"},
    "btn_other_courses": {"es": "Ver otros cursos", "en": "See other courses"},
    "btn_menu": {"es": "Menú principal", "en": "Main menu"},

    "prices_info": {
        "es": "*Precios*\n\n"
              "Cada curso tiene su propio costo por persona, desde $400 hasta $4,500 MXN, "
              "y los diplomados se manejan por paquetes o con inscripción más mensualidades.\n\n"
              "Dime el nombre del curso que te interesa y te doy el precio exacto, "
              "o toca *Ver opciones* para explorar el catálogo.",
        "en": "*Prices*\n\n"
              "Each course has its own price per person, from $400 to $4,500 MXN, "
              "and the diplomas are sold in packages or as an enrolment fee plus monthly instalments.\n\n"
              "Tell me which course interests you and I'll give you the exact price, "
              "or tap *View options* to explore the catalogue.",
    },
    "requirements_info": {
        "es": "*Requisitos de inscripción*\n\n"
              "Depende del curso:\n"
              "• *Cursos abiertos al público*: sin requisitos, desde 15 años.\n"
              "• *Cursos para profesionales de la salud*: ser estudiante o profesional del área, 18 años.\n"
              "• *Rescate*: personal operativo o conocimientos previos en primeros auxilios.\n"
              "• *Diplomados*: bachillerato o licenciatura terminada, según el programa.\n\n"
              "Dime qué curso te interesa y te confirmo sus requisitos exactos.",
        "en": "*Entry requirements*\n\n"
              "It depends on the course:\n"
              "• *Open public courses*: no requirements, from age 15.\n"
              "• *Health professional courses*: be a student or professional in the field, age 18.\n"
              "• *Rescue*: operational staff or prior first-aid knowledge.\n"
              "• *Diplomas*: completed high school or a degree, depending on the programme.\n\n"
              "Tell me which course interests you and I'll confirm its exact requirements.",
    },
    "contact_info": {
        "es": "*Contacto directo*\n\n"
              "Con gusto le paso tus datos al equipo de capacitación para que te contacten.\n"
              "Escribe *inscribirme* y te tomo tus datos, o cuéntame tu duda y la registro.\n\n"
              "Si se trata de una *emergencia médica*, llama al *911* de inmediato.",
        "en": "*Talk to the team*\n\n"
              "I can pass your details to the training team so they contact you.\n"
              "Type *enroll* and I'll take your details, or tell me your question and I'll log it.\n\n"
              "If this is a *medical emergency*, call *911* immediately.",
    },
    "faq_info": {
        "es": "*Preguntas frecuentes*\n\n"
              "• *¿Dan constancia?* Sí. Los cursos con evaluación entregan certificación "
              "(AHA, NAEMT, CONOCER según el curso); los demás entregan constancia de asistencia.\n"
              "• *¿Hay cursos en inglés?* La mayoría se imparte en español, con inglés bajo solicitud.\n"
              "• *¿Puedo llevar a mi equipo de trabajo?* Sí, tenemos cursos para brigadas de empresa "
              "y podemos armar grupos privados.\n"
              "• *¿Cuándo son las fechas?* El equipo de Cruz Roja confirma las fechas disponibles.\n\n"
              "Pregúntame lo que necesites saber.",
        "en": "*Frequently asked questions*\n\n"
              "• *Do you give a certificate?* Yes. Assessed courses issue certification "
              "(AHA, NAEMT or CONOCER depending on the course); the rest issue an attendance certificate.\n"
              "• *Are courses available in English?* Most are taught in Spanish, with English on request.\n"
              "• *Can I bring my work team?* Yes — we run company brigade courses and can arrange private groups.\n"
              "• *What are the dates?* The Cruz Roja team confirms available dates.\n\n"
              "Ask me anything you need.",
    },
    "language_switched": {
        "es": "Listo, seguimos en *español*. ¿En qué te ayudo?",
        "en": "Done, we'll continue in *English*. How can I help?",
    },
    "language_prompt": {
        "es": "¿En qué idioma prefieres continuar?",
        "en": "Which language would you like to continue in?",
    },

    # --- Qualify -------------------------------------------------------------
    "qualify_intro": {
        "es": "Antes de empezar, tomo tus datos para poder recomendarte los cursos correctos "
              "y que el equipo pueda contactarte. Son cinco datos rápidos.",
        "en": "Before we start, let me take your details so I can recommend the right courses "
              "and the team can contact you. Five quick things.",
    },
    "qualify_name": {
        "es": "¿Cuál es tu nombre completo?",
        "en": "What's your full name?",
    },
    "qualify_email": {
        "es": "¿Cuál es tu correo electrónico?",
        "en": "What's your email address?",
    },
    "qualify_email_retry": {
        "es": "Ese correo no parece válido. ¿Lo escribes de nuevo? (ejemplo: nombre@correo.com)",
        "en": "That email doesn't look valid. Could you type it again? (example: name@email.com)",
    },
    "qualify_phone": {
        "es": "¿A qué número de 10 dígitos te contactamos? (ej. 5512345678)",
        "en": "Which 10-digit number should we reach you on? (e.g. 5512345678)",
    },
    "qualify_phone_retry": {
        "es": "Ese número no parece de 10 dígitos. ¿Lo intentas de nuevo? (ej. 5512345678)",
        "en": "That doesn't look like a 10-digit number. Could you try again? (e.g. 5512345678)",
    },
    "qualify_address": {
        "es": "¿En qué ciudad o dirección te encuentras?",
        "en": "Which city or address are you in?",
    },
    "qualify_done": {
        "es": "¡Listo{name}, quedaron registrados tus datos!\n\n"
              "Ahora sí: ¿qué te gustaría hacer?",
        "en": "All set{name}, your details are saved!\n\n"
              "Now then: what would you like to do?",
    },
    "qualify_profile_header": {
        "es": "¿Para quién es la capacitación?",
        "en": "Who is the training for?",
    },
    "qualify_profile_body": {
        "es": "¿Cuál de estas opciones te describe mejor?",
        "en": "Which of these describes you best?",
    },
    "qualify_profile_button": {"es": "Elegir opción", "en": "Choose one"},
    "qualify_age": {
        "es": "¿Cuántos años tienes? (dato obligatorio)\n\n"
              "Te lo pregunto porque algunos cursos piden edad mínima de 15 años y otros de 18.\n"
              "Escribe tu edad o elige una opción.",
        "en": "How old are you? (required)\n\n"
              
    },
    "qualify_age_retry": {
        "es": "¿Me confirmas tu edad en números? (por ejemplo: 24)",
        "en": "Could you confirm your age in numbers? (for example: 24)",
    },
    "btn_age_under15": {"es": "Menos de 15", "en": "Under 15"},
    "btn_age_15_17": {"es": "15 a 17", "en": "15 to 17"},
    "btn_age_18plus": {"es": "18 o más", "en": "18 or older"},

    # --- Educate -------------------------------------------------------------
    "educate_intro": {
        "es": "¡Perfecto{name}! Con base en lo que me dices, estos son los cursos que te quedan:",
        "en": "Perfect{name}! Based on what you've told me, these are the courses that fit:",
    },
    "educate_hint": {
        "es": "Toca un curso para ver el detalle, o pregúntame lo que quieras.",
        "en": "Tap a course for the details, or ask me anything.",
    },
    "educate_under15": {
        "es": "Gracias por decirme. Nuestros cursos abiertos piden *15 años como mínimo*, "
              "así que todavía no podría inscribirte.\n\n"
              "Si un adulto quiere tomar el curso contigo o para tu escuela, con gusto lo vemos: "
              "escribe *inscribirme* y le paso tus datos al equipo.",
        "en": "Thanks for telling me. Our open courses require a *minimum age of 15*, "
              "so I can't sign you up just yet.\n\n"
              "If an adult wants to take the course with you or for your school, we'd be glad to "
              "help: type *enroll* and I'll pass your details to the team.",
    },
    "educate_none_for_profile": {
        "es": "Por tu edad, por ahora te corresponden nuestros cursos abiertos al público. "
              "Te muestro esos:",
        "en": "For your age, our open public courses are the ones available to you. Here they are:",
    },

    # --- Propose -------------------------------------------------------------
    "propose": {
        "es": "Por lo que me cuentas, *{course}* es el que mejor te queda:",
        "en": "From what you've told me, *{course}* is the best fit:",
    },
    "propose_question": {
        "es": "¿Te aparto un lugar en este curso?",
        "en": "Shall I reserve you a place on this course?",
    },
    "btn_yes_enroll": {"es": "Sí, inscribirme", "en": "Yes, sign me up"},
    "btn_see_others": {"es": "Ver otras opciones", "en": "See other options"},
    "age_warning": {
        "es": "\n\nNota: este curso pide *{min_age} años* como mínimo. Si aún no los cumples, "
              "el equipo puede orientarte sobre opciones para tu edad.",
        "en": "\n\nNote: this course requires a minimum age of *{min_age}*. If you're not there "
              "yet, the team can point you to options for your age.",
    },

    # --- Confirm -------------------------------------------------------------
    "confirm_which_course": {
        "es": "¡Con gusto! ¿En qué curso te inscribo?\n\n"
              "_(Escribe *cancelar* para detener, o pregúntame lo que quieras.)_",
        "en": "Happy to! Which course shall I sign you up for?\n\n"
              "_(Type *cancel* to stop, or ask me anything.)_",
    },
    # Courses the store does not sell online: same confirmation, but the team
    # arranges payment, so it does not claim a payment has been made.
    "confirm_done": {
        "es": "Gracias por registrarte en *{course}*,\n"
              "{name}, tu lugar para este curso ha sido reservado.\n"
              "Cualquier información adicional se compartirá en {email} y {phone}.",
        "en": "Thanks for registering for *{course}*,\n"
              "{name}, your seat for this course has been reserved.\n"
              "Any further information will be shared on {email} and {phone}.",
    },
    # --- Payment ------------------------------------------------------------
    # Sent between the yes and the confirmation, for the courses the store sells
    # online. Deliberately short: the button underneath is the point.
    "pay_now": {
        "es": "¡Listo{name}! Aparté tu lugar en *{course}*.\n\n"
              "{price}\n\n"
              "Toca el botón para completar tu pago en la tienda oficial de Cruz Roja "
              "y quedar inscrito.",
        "en": "Done{name}! I've held your place on *{course}*.\n\n"
              "{price}\n\n"
              "Tap the button to complete your payment on the official Cruz Roja store "
              "and secure your spot.",
    },
    "btn_pay_now": {"es": "Pagar e inscribirme", "en": "Pay & enroll"},
    "pay_now_footer": {
        "es": "Pago seguro · tienda.cruzrojacecem.com",
        "en": "Secure payment · tienda.cruzrojacecem.com",
    },
    # Sent right after the payment button. WhatsApp gives no callback when a
    # link button is tapped, so this cannot wait for the tap — it is sent
    # immediately and is what the person comes back to once payment is done.
    "confirm_done_paid": {
        "es": "Gracias por registrarte en *{course}*,\n"
              "{name}, tu lugar para este curso ha sido reservado.\n"
              "Cualquier información adicional se compartirá en {email} y {phone}.",
        "en": "Thanks for registering for *{course}*,\n"
              "{name}, your seat for this course has been reserved.\n"
              "Any further information will be shared on {email} and {phone}.",
    },

    "confirm_intro": {
        "es": "¡Excelente elección! Sólo necesito unos datos para que el equipo te contacte.",
        "en": "Excellent choice! I just need a few details so the team can contact you.",
    },

    "enroll_course": {
        "es": "¡Perfecto, vamos a inscribirte!\n¿Qué curso te interesa?\n\n_(Escribe *cancelar* para detener, o pregúntame lo que quieras: retomamos donde nos quedamos.)_",
        "en": "Great, let's get you enrolled!\nWhich course are you interested in?\n\n_(Type *cancel* to stop, or ask me anything — we'll pick up where we left off.)_",
    },
    "enroll_name": {
        "es": "Anotado: *{course}*.\n¿Cuál es tu nombre completo?",
        "en": "Noted: *{course}*.\nWhat's your full name?",
    },
    "enroll_email": {
        "es": "Mucho gusto, {name}. ¿Cuál es tu correo electrónico?",
        "en": "Nice to meet you, {name}. What's your email address?",
    },
    "enroll_email_retry": {
        "es": "Ese correo no parece válido. ¿Lo escribes de nuevo? (ejemplo: nombre@correo.com)",
        "en": "That email doesn't look valid. Could you type it again? (example: name@email.com)",
    },
    "enroll_phone": {
        "es": "Gracias. ¿A qué número de 10 dígitos te contactamos? (ej. 5512345678)",
        "en": "Thanks. Which 10-digit number should we reach you on? (e.g. 5512345678)",
    },
    "enroll_phone_retry": {
        "es": "Ese número no parece de 10 dígitos. ¿Lo intentas de nuevo? (ej. 5512345678)",
        "en": "That doesn't look like a 10-digit number. Could you try again? (e.g. 5512345678)",
    },
    "enroll_address": {
        "es": "¡Ya casi! ¿En qué ciudad o dirección te encuentras?",
        "en": "Almost done! Which city or address are you in?",
    },
    "enroll_complete": {
        "es": "¡Listo, {name}! Registramos tu interés en *{course}*.\n\n"
              "El equipo de capacitación te contactará en {email} / {phone} para confirmarte "
              "fechas, sede y forma de pago.\n\n"
              "Mientras tanto, puedes seguir preguntándome lo que necesites.",
        "en": "All set, {name}! We've registered your interest in *{course}*.\n\n"
              "The training team will contact you at {email} / {phone} to confirm "
              "dates, location and payment.\n\n"
              "Meanwhile, feel free to keep asking me anything.",
    },
    "enroll_cancelled": {
        "es": "Sin problema, cancelé la inscripción. Sigo aquí para lo que necesites.",
        "en": "No problem, I've cancelled the sign-up. I'm here whenever you need me.",
    },
    "resume_note": {
        "es": "Continuemos con tu inscripción:",
        "en": "Back to your sign-up:",
    },
    "chat_hint": {
        "es": "Escribe *menú* para ver todas las opciones, o *inscribirme* para registrarte.",
        "en": "Type *menu* to see all options, or *enroll* to sign up.",
    },
    "unsupported_media": {
        "es": "Por ahora sólo puedo leer mensajes de texto. Escríbeme tu pregunta o toca *menú* para ver las opciones.",
        "en": "For now I can only read text messages. Type your question, or tap *menu* to see the options.",
    },
    "rate_limited": {
        "es": "Estoy recibiendo muchos mensajes tuyos seguidos. Dame un momento y vuelve a escribirme.",
        "en": "I'm getting a lot of messages from you at once. Give me a moment and message me again.",
    },
    "error_retry": {
        "es": "Algo falló de mi lado. ¿Me lo repites, por favor?",
        "en": "Something went wrong on my end. Could you send that again, please?",
    },
    "no_courses_in_group": {
        "es": "Aún no tengo cursos cargados en esa categoría. Toca *menú* para ver las demás.",
        "en": "I don't have courses loaded in that category yet. Tap *menu* to see the others.",
    },
}

# Kept short on purpose: a menu row title is 24 characters including the count
# that gets appended to it, e.g. "Personal de salud (33)".
GROUP_LABELS = {
    "public":        {"es": "Público general",     "en": "General public"},
    "employees":     {"es": "Empresas",            "en": "Companies"},
    "health":        {"es": "Personal de salud",   "en": "Health staff"},
    "rescue":        {"es": "Rescate",             "en": "Rescue"},
    "international": {"es": "Cert. internacional", "en": "Intl. certs"},
    "diploma":       {"es": "Diplomados",          "en": "Diplomas"},
    "certification": {"es": "Cert. CONOCER",       "en": "CONOCER cert."},
    "instructor":    {"es": "Instructores",        "en": "Instructor training"},
}

GROUP_DESCRIPTIONS = {
    "public":        {"es": "Primeros auxilios, RCP, heridas", "en": "First aid, CPR, wound care"},
    "employees":     {"es": "Brigadas para empresas",          "en": "Workplace brigade training"},
    "health":        {"es": "Actualización clínica y prehospitalaria", "en": "Clinical & prehospital skills"},
    "rescue":        {"es": "Vertical, acuático, vehicular",   "en": "Vertical, water, vehicle"},
    "international": {"es": "AHA y NAEMT: BLS, ACLS, PHTLS",   "en": "AHA & NAEMT: BLS, ACLS, PHTLS"},
    "diploma":       {"es": "Programas largos con diploma UNAM/Anáhuac", "en": "Long programmes with UNAM/Anáhuac diploma"},
    "certification": {"es": "Estándar de competencia laboral", "en": "Labour competency standard"},
    "instructor":    {"es": "Forma a nuevos instructores",     "en": "Train new instructors"},
}


# The qualifying question's options. Each maps to a slice of the catalogue.
PROFILES = {
    "public": {
        "group": "public",
        "label": {"es": "Para mí o mi familia", "en": "For me or my family"},
        "desc": {"es": "Primeros auxilios, RCP, heridas", "en": "First aid, CPR, wound care"},
    },
    "employees": {
        "group": "employees",
        "label": {"es": "Para mi empresa", "en": "For my company"},
        "desc": {"es": "Brigadas y protección civil", "en": "Workplace brigades"},
    },
    "health": {
        "group": "health",
        "label": {"es": "Soy del área de salud", "en": "I work in health"},
        "desc": {"es": "Actualización clínica y prehospitalaria", "en": "Clinical & prehospital"},
    },
    "rescue": {
        "group": "rescue",
        "label": {"es": "Rescate profesional", "en": "Professional rescue"},
        "desc": {"es": "Vertical, acuático, vehicular", "en": "Vertical, water, vehicle"},
    },
    "diploma": {
        "group": "diploma",
        "label": {"es": "Quiero un diplomado", "en": "I want a diploma"},
        "desc": {"es": "Programas largos con diploma universitario", "en": "Long university programmes"},
    },
    "browsing": {
        "group": None,
        "label": {"es": "Sólo tengo una duda", "en": "I just have a question"},
        "desc": {"es": "Pregúntame lo que necesites", "en": "Ask me anything"},
    },
}


def t(key: str, language: str, /, **kwargs) -> str:
    """Look up a line and fill in its placeholders.

    `language` is positional-only on purpose: several templates have their own
    `{language}` field (a course's language of instruction), and without this
    that placeholder would collide with this function's own argument.
    """
    text = COPY[key].get(language, COPY[key]["es"])
    return text.format(**kwargs) if kwargs else text
