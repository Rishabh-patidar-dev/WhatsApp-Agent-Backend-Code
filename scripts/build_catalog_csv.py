"""Builds data/juptr_rc_courses.csv from the client's course sheet.

The client (Cruz Roja) delivers the catalogue as an Excel workbook. This script
encodes the same 67 rows in a compact form — shared values are declared once per
course family, only the genuinely per-course values are spelled out — and writes
the flat CSV that the ingestion pipeline consumes.

Run once whenever a new sheet arrives:  python scripts/build_catalog_csv.py

Row order and every column here were transcribed from JUPTRxRC_Courses (the
client's sheet) and cross-checked against JUPTRxRC_Courses_Data_Guide:
  - 38 rows require "Estudiante o profesional del área de la salud"
  - 13 rows are Spanish-only, the other 54 offer English on request
  - 12 General Public rows cap at 20 participants (min 5)
  - GP009 is a 920-hour diploma despite its General Public prefix
  - HP014/HP015 share one description verbatim (flagged, see FLAGS below)
  - Printed manuals belong to HP033-HP037, HP039, HP040 (NAEMT/AHA accreditation)
"""
import csv
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "juptr_rc_courses.csv"

SPA_EN = "Español, inglés bajo solicitud de servicio"
SPA = "Español"
CRM = "Constancia de Cruz Roja Mexicana"
NAEMT = "Certificado y credencial NAEMT (National Association of Emergency Medical Technicians)"
AHA = "Credencial AHA (American Heart Association)"
UNAM = "Constancia de Cruz Roja Mexicana + Diploma de la Universidad Nacional Autónoma de México (UNAM)"
NO_EXAM = ("No hay evaluación", "No aplica")
EXAM_74 = ("Evaluación teórica y evaluación práctica", "74%")
EXAM_T74 = ("Evaluación teórica", "74%")
EXAM_76 = ("Evaluación teórica y evaluación práctica", "Teórica 76/100 + evaluación práctica")
EXAM_80 = ("Evaluación teórica y evaluación continua", "80%")
EXAM_80_ASYNC = (
    "Completar el 100% de las actividades asincrónicas de la plataforma "
    "y asistencia mínima al 80% de las prácticas presenciales",
    "80%",
)
OPEN_PUBLIC = "No, es completamente abierto a cualquier público"
COMPANY = "No, para empleados de empresas"
HEALTH_PRO = "Estudiante o profesional del área de la salud"
NO_EDU = "Sin requisitos específicos"
TUM = "Constancia de técnico en urgencias médicas"
BRANCH_CAP = "Capacidad por unidad formadora (varía por sede)"

# Shared defaults per course family. Any row can override any of these.
FAMILIES = {
    "GP": dict(
        category="General Public", track="Capacitación Externa (Población General)",
        prereq=OPEN_PUBLIC, age="15", education=NO_EDU, days="1", schedule="1 sesión",
        delivery="Presencial", language=SPA_EN, materials="Material para prácticas",
        assessment=NO_EXAM[0], passing=NO_EXAM[1], max_p="20 (grupos más grandes bajo solicitud)",
        min_p="5", credential=CRM,
    ),
    "GPE": dict(
        category="General Public (Employees)", track="Capacitación Externa (Población General)",
        prereq=COMPANY, age="15", education=NO_EDU, days="1", schedule="1 sesión",
        delivery="Presencial", language=SPA_EN, materials="Material para prácticas",
        assessment=NO_EXAM[0], passing=NO_EXAM[1], max_p="20 (grupos más grandes bajo solicitud)",
        min_p="5", credential=CRM,
    ),
    "HP": dict(
        category="Health Professionals Continuing Skill-Development",
        track="Capacitación Profesional en Salud", prereq=HEALTH_PRO, age="18",
        education=NO_EDU, days="1", schedule="1 sesión", delivery="Presencial",
        language=SPA_EN, materials="Manual digital", assessment=NO_EXAM[0],
        passing=NO_EXAM[1], max_p="18", min_p="8", credential=CRM,
    ),
    "RP": dict(
        category="Rescue Professionals", track="Capacitación Profesional en Rescate",
        prereq=HEALTH_PRO, age="18", education=NO_EDU, days="3", schedule="3 Sesiones",
        delivery="Presencial", language=SPA, materials="Manual digital",
        assessment=EXAM_74[0], passing=EXAM_74[1], max_p="24", min_p="12", credential=CRM,
    ),
    "INTL": dict(
        category="Health Professionals Continuing Skill-Development",
        track="Certificación Internacional", prereq=HEALTH_PRO, age="18", education=NO_EDU,
        days="2", schedule="2 Sesiones", delivery="Presencial", language=SPA_EN,
        materials="Manual físico", assessment=EXAM_T74[0], passing=EXAM_T74[1],
        max_p="20", min_p="8", credential=NAEMT,
    ),
    "DIP": dict(
        category="Health Professionals Continuing Skill-Development",
        track="Diplomado", prereq=HEALTH_PRO, age="18", education="Bachillerato terminado",
        delivery="Híbrido", language=SPA, materials="Plataforma educativa para contenidos y actividades académicas",
        assessment=EXAM_80_ASYNC[0], passing=EXAM_80_ASYNC[1], max_p="60", min_p="12",
        credential=UNAM,
    ),
}

# (id, family, name_es, name_en, hours, span, price, description, audience, **overrides)
ROWS = [
    ("GP001", "GP", "Primeros Auxilios Nivel Básico", "First Aid – Basic Level", "8", "1 día", "755",
     "Curso diseñado para que personas que no son profesionales de la salud tengan un primer acercamiento a las acciones que pueden…",
     "Personas que no son profesionales de la salud",
     dict(materials="Manual digital y material para prácticas",
          credential=CRM + " / DC-3 de competencias laborales de la STPS para empleados de empresas")),
    ("GP002", "GP", "Toma de Signos Vitales", "Vital Signs Measurement", "6", "1 día", "455",
     "Los signos vitales son valores que permiten estimar la efectividad de la circulación, la respiración y las funciones neurológicas…",
     "Población en general", {}),
    ("GP003", "GP", "Reanimación Cardiopulmonar y Desobstrucción de la Vía Aérea",
     "Cardiopulmonary Resuscitation and Airway Obstruction Relief", "5", "0.5 día (medio día)", "475",
     "La reanimación cardiopulmonar y la desobstrucción de la vía aérea son destrezas del soporte vital básico, dirigidas a cualquier persona…",
     "Cualquier persona que necesite aprender cómo actuar ante un paro cardiorrespiratorio", {}),
    ("GP004", "GP", "Cuidado de Pacientes en el Hogar (Curaciones, Administración de Medicamentos)",
     "Home Patient Care (Wound Care, Medication Administration)", "6", "1 día", "555",
     "Curso dirigido a personas que necesitan conocer las adaptaciones necesarias para cuidar a enfermos en casa, las recomendaciones…",
     "Personas que necesitan tener conocimientos para cuidar a un enfermo en casa", {}),
    ("GP005", "GP", "Limpieza y Tratamiento de Heridas", "Wound Cleaning and Treatment", "4", "0.5 día (medio día)", "425",
     "Curso que permite conocer los principios de la identificación de heridas contaminadas y su correcta desinfección y limpieza.",
     "Personas que realizan intervenciones que requieren manejo de heridas", {}),
    ("GP006", "GP", "Formación de Camilleros (Seguridad en el Traslado y Movilización de Pacientes)",
     "Stretcher-Bearer Training (Safe Patient Transfer and Mobilization)", "16", "2 días", "1500",
     "Curso de formación que permite aprender los principios de la movilización segura de pacientes, tanto en dispositivos móviles…",
     "Población en general", dict(days="2", schedule="2 Sesiones")),
    ("GP007", "GP", "Primeros Auxilios Pediátricos", "Pediatric First Aid", "8", "1 día", "780",
     "Curso diseñado para que personas que no son profesionales de la salud tengan un primer acercamiento a las acciones que pueden…",
     "Personas que necesitan tener conocimientos para atender a un menor", {}),
    ("GPE001", "GPE", "Brigada de Búsqueda y Rescate", "Search and Rescue Brigade", "8", "1 día", "1000",
     "Este curso está diseñado para capacitar a brigadistas y personal de emergencias para localizar, extraer y trasladar a personas atrapadas…",
     "Empleados de empresas que necesitan formar brigadas", {}),
    ("GPE002", "GPE", "Brigada de Uso y Manejo de Extintores", "Fire Extinguisher Use and Handling Brigade", "8", "1 día", "1655",
     "Este curso está diseñado para capacitar a brigadistas y personal de emergencias en conocimientos teóricos y habilidades prácticas…",
     "Empleados de empresas que necesitan formar brigadas", {}),
    ("GPE003", "GPE", "Brigada de Evacuación de Inmuebles", "Building Evacuation Brigade", "8", "1 día", "820",
     "Este curso está diseñado para capacitar a brigadistas y personal de emergencias en conocimientos teóricos y habilidades prácticas…",
     "Empleados de empresas que necesitan formar brigadas", {}),
    ("GPE004", "GPE", "Brigada de Primeros Auxilios", "First Aid Brigade", "8", "1 día", "755",
     "Este curso está diseñado para capacitar a brigadistas y personal de emergencias en conocimientos teóricos y habilidades prácticas…",
     "Empleados de empresas que necesitan formar brigadas",
     dict(prereq=OPEN_PUBLIC, materials="Manual digital y material para prácticas")),
    ("GP008", "GP", "Primeros Auxilios en el Deporte", "First Aid in Sports", "8", "1 día", "1100",
     "Este curso está diseñado para capacitar a personas no profesionales de la salud que participan en deportes como entrenadores o…",
     "Personas que necesitan tener conocimientos para atender urgencias en el deporte",
     dict(materials="Manual digital y material para prácticas")),

    ("HP001", "HP", "Accesos Intraóseos", "Intraosseous Access", "6", "1 día", "650",
     "Revisión de la destreza de acceso intraóseo para la atención de pacientes críticos, con aplicación a nivel hospitalario y…",
     "Profesionales de la salud", {}),
    ("HP002", "HP", "Atención al Parto Eutócico", "Eutocic (Normal) Childbirth Care", "6", "1 día", "1000",
     "Fortalecer las competencias del personal prehospitalario para la atención segura, oportuna y humanizada del parto eutócico…",
     "Profesionales de la salud", {}),
    ("HP003", "HP", "Atención Integral del Paciente Neurocrítico", "Comprehensive Care of the Neurocritical Patient", "16", "1 día", "800",
     "Curso para integrar escalas de valoración de gravedad y diagnósticas que permitan reconocer y tratar las alteraciones…",
     "Profesionales de la salud", {}),
    ("HP004", "HP", "Primeros Auxilios para Mascotas", "Pet First Aid", "8", "1 día", "1350",
     "Identificar, comprender y aplicar técnicas básicas de primeros auxilios para mascotas, actuando de manera segura y responsable…",
     "Profesionales de la salud", {}),
    ("HP005", "HP", "Atención Integral del Paciente Quemado", "Comprehensive Care of the Burn Patient", "10", "1 día", "850",
     "Curso que brinda los fundamentos basados en la mejor evidencia clínica disponible para el tratamiento de pacientes quemados…",
     "Profesionales de la salud", {}),
    ("HP006", "HP", "Ecografía Prehospitalaria", "Prehospital Ultrasound", "12", "1 día", "1100",
     "Curso que forma a los profesionales de la salud en la ejecución autónoma de evaluaciones ecográficas en pacientes…",
     "Profesionales de la salud", {}),
    ("HP007", "HP", "Electrocardiografía Básica", "Basic Electrocardiography", "8", "1 día", "650",
     "Curso que acerca al profesional de la salud a una revisión inicial del funcionamiento e importancia de la interpretación…",
     "Profesionales de la salud", dict(delivery="Presencial o en línea")),
    ("HP008", "HP", "Electrocardiografía Avanzada", "Advanced Electrocardiography", "14", "1 día", "750",
     "Principios y conocimientos de la interpretación del electrocardiograma de 12 derivaciones en el ámbito de urgencias…",
     "Profesionales de la salud", {}),
    ("HP009", "HP", "Terapia Intravenosa (Fluidoterapia Básica)", "Intravenous Therapy (Basic Fluid Therapy)", "6", "1 día", "650",
     "Curso para aplicar de manera segura y eficaz las técnicas avanzadas de acceso venoso y administración de soluciones…",
     "Profesionales de la salud", {}),
    ("HP010", "HP", "Fluidoterapia Avanzada", "Advanced Fluid Therapy", "6", "1 día", "650",
     "Utilizar la medicina basada en evidencia para la correcta utilización de los accesos intravenosos para la infusión de líquidos y…",
     "Profesionales de la salud", {}),
    ("HP011", "HP", "Introducción al Soporte Vital Avanzado", "Introduction to Advanced Life Support", "8", "1 día", "500",
     "Conocimientos sobre la interacción de los TUM básicos con otro personal de la salud y las destrezas que debe realizar un equipo de…",
     "Profesionales de la salud", {}),
    ("HP012", "HP", "Manejo Integral de la Vía Aérea", "Comprehensive Airway Management", "8", "1 día", "800",
     "Herramientas basadas en evidencia para la evaluación y manejo integral de la vía aérea en el ambiente prehospitalario e…",
     "Profesionales de la salud", {}),
    ("HP013", "HP", "Manejo de Urgencias Cardiovasculares", "Management of Cardiovascular Emergencies", "6", "1 día", "700",
     "Identificar las principales actualizaciones a la identificación y tratamiento de urgencias cardiovasculares en la atención…",
     "Profesionales de la salud", {}),
    ("HP014", "HP", "Urgencias Clínicas", "Clinical Emergencies", "6", "1 día", "800",
     "Identificar las principales actualizaciones a la identificación y tratamiento de urgencias cardiovasculares en la atención pre…",
     "Profesionales de la salud", {}),
    ("HP015", "HP", "Urgencias Traumatológicas", "Trauma Emergencies", "6", "1 día", "800",
     "Identificar las principales actualizaciones a la identificación y tratamiento de urgencias cardiovasculares en la atención pre…",
     "Profesionales de la salud", {}),
    ("HP016", "HP", "Urgencias Obstétricas", "Obstetric Emergencies", "14", "1 día", "1100",
     "Capacitar al personal en salud, con énfasis en los técnicos en urgencias médicas, en la atención de pacientes gineco-obstétricos…",
     "Profesionales de la salud", {}),
    ("HP017", "HP", "Urgencias Pediátricas", "Pediatric Emergencies", "14", "1 día", "950",
     "Desarrollar las habilidades teóricas y prácticas necesarias para la evaluación, estabilización y atención integral del paciente pediátrico…",
     "Profesionales de la salud", {}),
    ("HP018", "HP", "Manejo de Urgencias Metabólicas", "Management of Metabolic Emergencies", "6", "1 día", "700",
     "Identificar las principales actualizaciones a la identificación y tratamiento de urgencias metabólicas en la atención prehospitalaria.",
     "Profesionales de la salud", {}),
    ("HP019", "HP", "Inmovilización Selectiva y Manejo del Paciente con Sospecha de Lesión Espinal",
     "Selective Immobilization and Management of the Patient with Suspected Spinal Injury", "8", "1 día", "700",
     "Herramientas para la aplicación y ejecución de los algoritmos de inmovilización espinal selectiva y/o restricción del movimiento…",
     "Profesionales de la salud", {}),
    ("HP020", "HP", "Introducción al Manejo Farmacológico", "Introduction to Pharmacological Management", "6", "1 día", "650",
     "Incorporar una forma sistemática para aplicar conocimientos de fisiología celular en el mecanismo farmacológico de distintos…",
     "Profesionales de la salud", dict(delivery="Presencial y en línea")),
    ("HP021", "HP", "Uso de Medicamentos Vasoactivos", "Use of Vasoactive Drugs", "6", "1 día", "650",
     "Incorporar una forma sistemática para aplicar conocimientos de fisiología celular en el mecanismo farmacológico de distintos…",
     "Profesionales de la salud", {}),
    ("HP022", "HP", "Stop The Bleed (Control de Hemorragias)", "Stop The Bleed (Bleeding Control)", "5", "0.5 día (medio día)", "400",
     "Herramientas basadas en evidencia para la evaluación y contención de hemorragias mediante las diferentes técnicas que existen.",
     "Profesionales de la salud", dict(credential="Constancia del American College of Surgeons")),
    ("HP023", "HP", "Accesos Vasculares Ecoguiados", "Ultrasound-Guided Vascular Access", "8", "1 día", "800",
     "Integrar los conocimientos teóricos y habilidades prácticas necesarias para la localización, selección y canalización de accesos…",
     "Profesionales de la salud", {}),
    ("HP024", "HP", "Analgesia y Manejo del Dolor", "Analgesia and Pain Management", "8", "1 día", "800",
     "Fortalecer las competencias del personal prehospitalario para evaluar, clasificar y manejar adecuadamente el dolor agudo…",
     "Profesionales de la salud", {}),
    ("HP025", "HP", "Reanimación Neonatal en Prehospitalario", "Neonatal Resuscitation in the Prehospital Setting", "16", "1 día", "1000",
     "Capacitar al personal prehospitalario en la identificación oportuna de recién nacidos que requieren intervención…",
     "Profesionales de la salud", {}),
    ("HP026", "HP", "Intervención en Crisis", "Crisis Intervention", "12", "1 día", "800",
     "Conocimientos y herramientas básicas para identificar reacciones emocionales agudas y aplicar estrategias de intervención en crisis…",
     "Profesionales de la salud", {}),
    ("HP027", "HP", "Atención Integral del Paciente de Trauma", "Comprehensive Care of the Trauma Patient", "8", "1 día", "800",
     "Capacitar al personal prehospitalario en la evaluación rápida, toma de decisiones y aplicación de intervenciones prioritarias…",
     "Profesionales de la salud", {}),
    ("HP028", "HP", "Ventilación Mecánica", "Mechanical Ventilation", "8", "1 día", "800",
     "Conocimientos y habilidades necesarias para la indicación, configuración básica, monitoreo y manejo seguro de la ventilación…",
     "Profesionales de la salud", {}),
    ("HP029", "HP", "Ventilación Mecánica No Invasiva", "Non-Invasive Mechanical Ventilation", "8", "1 día", "800",
     "Conocimientos y habilidades necesarias para la indicación, configuración básica, monitoreo y manejo seguro de la ventilación…",
     "Profesionales de la salud", {}),
    ("HP030", "HP", "Traslado Interhospitalario del Paciente Crítico", "Interhospital Transfer of the Critical Patient", "16", "1 día", "800",
     "Capacitar al personal de salud en los principios, preparación y ejecución segura del traslado interhospitalario del paciente…",
     "Profesionales de la salud", {}),
    ("HP031", "HP", "Curso Operador de Vehículos de Emergencia (COVE)", "Emergency Vehicle Operator Course", "24", "3 días", "2800",
     "Promover y desarrollar técnicas de conducción seguras en los operadores de ambulancias, a fin de disminuir los accidentes viales…",
     "Profesionales de la salud",
     dict(days="3", schedule="3 Sesiones", assessment=EXAM_74[0], passing=EXAM_74[1], max_p="24", min_p="12")),

    ("RP001", "RP", "Especialista en Rescate Vertical", "Vertical Rescue Specialist", "32", "4 días", "4500",
     "Destrezas para la realización de rescates verticales con el uso de cuerdas y la implementación de tirolesas y trípies.",
     "Rescue professional", dict(days="4", schedule="4 Sesiones")),
    ("RP002", "RP", "Rescate Vertical Nivel Operaciones", "Vertical Rescue — Operations Level", "24", "3 días", "3500",
     "Destrezas para la realización de rescates verticales, las destrezas para el rescate 1 a 1 y las técnicas de ascenso y descenso.",
     "Rescue professional", {}),
    ("RP003", "RP", "Técnico en Extracción Vehicular (TEV)", "Vehicle Extrication Technician", "24", "3 días", "3900",
     "Generar las destrezas para el manejo de equipo de extracción vehicular para el rescate de pacientes que sufrieron un accidente…",
     "Rescue professional", dict(prereq="Personal operativo", education=TUM)),
    ("RP004", "RP", "Guardavidas Profesional en Aguas Abiertas", "Professional Open Water Lifeguard", "24", "3 días", "2600",
     "Conocimientos para el rescate de pacientes en áreas acuáticas de difícil acceso.",
     "Profesionales de la salud",
     dict(prereq="Personas interesadas en desarrollar habilidades de rescate",
          education="Conocimientos previos en primeros auxilios y habilidades acuáticas")),
    ("RP005", "RP", "Guardavidas de Albercas y Parques Acuáticos", "Pool and Water Park Lifeguard", "24", "3 días", "2600",
     "Conocimientos para el rescate de pacientes en albercas y centros acuáticos.",
     "Profesionales de la salud",
     dict(prereq="Personas interesadas en desarrollar habilidades de rescate",
          education="Conocimientos previos en primeros auxilios y habilidades acuáticas")),
    ("HP032", "HP", "TRIAGE y Manejo de Múltiples Víctimas", "TRIAGE and Multiple Casualty Management", "24", "3 días", "2400",
     "Generar las destrezas para la identificación de los métodos para clasificación de pacientes y el establecimiento de un comando de…",
     "Profesionales de la salud",
     dict(days="3", schedule="3 Sesiones", language=SPA, assessment=EXAM_74[0], passing=EXAM_74[1],
          max_p="24", min_p="12", prereq="Personal operativo en atención de urgencias", education=TUM)),

    ("HP033", "INTL", "Prehospital Trauma Life Support (PHTLS)", "Prehospital Trauma Life Support (PHTLS)", "16", "2 días", "2700",
     "Generar las destrezas para la atención de pacientes traumatizados, basados en las actualizaciones más actuales…",
     "Profesionales de la salud",
     dict(prereq="Personal operativo en atención de urgencias y profesionales de la salud")),
    ("HP034", "INTL", "Advanced Medical Life Support (AMLS)", "Advanced Medical Life Support (AMLS)", "16", "2 días", "2700",
     "Generar las destrezas para la atención de pacientes con emergencias clínicas con el máximo estándar de educación…",
     "Profesionales de la salud", {}),
    ("HP035", "INTL", "Emergency Pediatric Care (EPC)", "Emergency Pediatric Care (EPC)", "16", "2 días", "2700",
     "Generar las destrezas para la atención de pacientes pediátricos basados en la mejor evidencia científica disponible…",
     "Profesionales de la salud", {}),
    ("HP036", "INTL", "EMS Vehicle Operator Safety (EVOS)", "EMS Vehicle Operator Safety (EVOS)", "16", "2 días", "2600",
     "Generar las destrezas para la conducción segura de vehículos de emergencia, con la oportunidad de obtener certificación…",
     "Profesionales de la salud", dict(prereq="Personal operativo")),
    ("HP037", "INTL", "Geriatric Education for EMS (GEMS)", "Geriatric Education for EMS (GEMS)", "8", "1 día", "2000",
     "Generar las destrezas para la atención de pacientes geriátricos, contemplando las características propias de este grupo de edad.",
     "Profesionales de la salud", dict(days="1", schedule="1 sesión")),
    ("HP038", "INTL", "Basic Life Support (BLS)", "Basic Life Support (BLS)", "6", "0.5 día (medio día)", "1900",
     "El entrenamiento de soporte vital básico (SVB/BLS) refuerza los conocimientos de los profesionales de la salud acerca de la…",
     "Profesionales de la salud",
     dict(days="1", schedule="1 sesión", materials="Manual digital", credential=AHA,
          assessment=EXAM_76[0], passing=EXAM_76[1], max_p="20", min_p="7",
          prereq="Población en general y profesionales de la salud")),
    ("HP039", "INTL", "Advanced Cardiovascular Life Support (ACLS)", "Advanced Cardiovascular Life Support (ACLS)", "12", "2 días", "2900",
     "El curso SVCA/ACLS está diseñado para profesionales de la salud que dirigen o participan en el manejo de paro cardiorrespiratorio…",
     "Profesionales de la salud que dirigen o participan en la reanimación",
     dict(credential=AHA, assessment=EXAM_76[0], passing=EXAM_76[1], max_p="20", min_p="7")),
    ("HP040", "INTL", "Pediatric Advanced Life Support (PALS)", "Pediatric Advanced Life Support (PALS)", "18", "2 días", "3850",
     "El curso de Reanimación Pediátrica (PALS) está diseñado para instruir acerca del reconocimiento y prevención de probables…",
     "Profesionales de la salud",
     dict(credential=AHA, assessment=EXAM_76[0], passing=EXAM_76[1], max_p="20", min_p="7")),

    ("GP009", "DIP", "Diplomado de Atención Médica Prehospitalaria Nivel Básico (DAMP-B)",
     "Diploma in Basic Prehospital Emergency Medical Care", "920", "236 días (12 meses)", "Por paquete: consultar",
     "El Diplomado de Técnico en Urgencias Médicas Nivel Básico consiste en una formación teórico-práctica orientada al desarrollo de…",
     "Personas interesadas en desarrollar habilidades en urgencias médicas",
     dict(category="General Public", track="Capacitación Externa (Población General)", days="12 meses (365 días)",
          schedule="Entre semana matutino y vespertino y fines de semana", delivery="Presencial",
          prereq="Mayor de 18 años, CURP y certificado de bachillerato",
          materials="Material y equipo de simulación para prácticas y escenarios de atención prehospitalaria",
          assessment=EXAM_80[0], passing=EXAM_80[1], max_p=BRANCH_CAP, min_p=BRANCH_CAP,
          credential="Depende del paquete elegido (incluye Constancia Cruz Roja Mexicana y Diploma Universidad Anáhuac; algunos paquetes añaden credencial AHA y NAEMT)")),
    ("HP041", "DIP", "Diplomado de Atención Médica Prehospitalaria Nivel Avanzado (DAMP-A)",
     "Diploma in Advanced Prehospital Emergency Medical Care", "624", "72 sábados (18 meses)", "Por paquete: consultar",
     "El Diplomado en Atención Médica Prehospitalaria Nivel Avanzado (DAMP-A) es un programa de formación teórico-práctica que…",
     "Técnicos en urgencias médicas, básicos o intermedios",
     dict(days="18 meses (78 días)", schedule="Sábados de 9:00 a 18:00", delivery="Híbrido",
          prereq="Constancia vigente de Técnico en Urgencias Médicas Básico",
          materials="Material y equipo de simulación para prácticas y escenarios de atención prehospitalaria",
          assessment=EXAM_80[0], passing=EXAM_80[1], max_p=BRANCH_CAP, min_p=BRANCH_CAP,
          credential="Depende del paquete elegido (Constancia Cruz Roja Mexicana, Diploma Universidad Anáhuac, credencial AHA y NAEMT)")),
    ("HP042", "DIP", "Diplomado: Apoyo Psicosocial en Emergencias Médicas y Desastres",
     "Diploma in Psychosocial Support in Medical Emergencies and Disasters", "203", "25 sábados (6 meses)",
     "Inscripción 2000 + mensualidades (consultar)",
     "Diplomado de 6 meses, avalado por la Subdivisión de Graduados y Educación Continua de la Facultad de Medicina de la UNAM…",
     "Profesionales de la salud",
     dict(days="6 meses", schedule="Sábados de 9:00 a 14:00", delivery="En línea (síncrono)",
          prereq="Carrera técnica en urgencias médicas o licenciatura del área de la salud",
          education="Licenciatura o carrera técnica")),
    ("HP043", "DIP", "Diplomado: Fisioterapia en el Paciente Hospitalizado",
     "Diploma in Physiotherapy for Hospitalized Patients", "204", "39 sábados (9 meses)",
     "Inscripción 3000 + mensualidades (consultar)",
     "Diplomado de 9 meses, avalado por la Subdivisión de Graduados y Educación Continua de la Facultad de Medicina de la UNAM, dirigido…",
     "Profesionales de la salud en fisioterapia",
     dict(days="9 meses", schedule="Sábados de 9:00 a 16:00",
          prereq="Licenciatura en fisioterapia, rehabilitación física o kinesiología", education="Licenciatura",
          materials="Material y equipo de simulación para prácticas y escenarios de fisioterapia intrahospitalaria")),
    ("HP044", "DIP", "Diplomado: Fisioterapia Respiratoria", "Diploma in Respiratory Physiotherapy", "204", "39 sábados (9 meses)",
     "Inscripción 3000 + mensualidades (consultar)",
     "Diplomado de 9 meses, avalado por la Subdivisión de Graduados y Educación Continua de la Facultad de Medicina de la UNAM, para…",
     "Profesionales de la salud en fisioterapia",
     dict(days="9 meses", schedule="Sábados de 9:00 a 16:00",
          prereq="Licenciatura en fisioterapia, rehabilitación física o kinesiología", education="Licenciatura",
          materials="Material y equipo de simulación para prácticas y escenarios de fisioterapia respiratoria")),

    ("RP006", "RP", "Cartografía de Emergencias Nivel Operaciones", "Emergency Mapping – Operational Level", "24", "3 días", "1500",
     "Curso enfocado en el uso de herramientas cartográficas y de geolocalización aplicadas a la gestión de emergencias…",
     "Rescue professional",
     dict(prereq="Personal operativo", education="Constancia de técnico en urgencias médicas o cursos de primeros auxilios")),
    ("IR001", "RP", "Programa de Formación de Instructores", "Instructor Training Program", "24", "3 días", "2200",
     "Programa diseñado para desarrollar las competencias pedagógicas, técnicas y de evaluación necesarias para que personal con…",
     "Instructor",
     dict(category="Instructor", track="Capacitación Profesional en Salud y Educación",
          prereq="Personal operativo y voluntario", education="Constancia de Técnico en Urgencias Médicas",
          assessment=EXAM_T74[0], passing=EXAM_T74[1])),

    ("HP045", "HP", "RQI (Resuscitation Quality Improvement)", "RQI – Resuscitation Quality Improvement", "6", "1 día", "2900",
     "Modelo híbrido y continuo en reanimación cardiopulmonar que sustituye los largos cursos presenciales por sesiones en línea y…",
     "Profesionales de la salud",
     dict(schedule="Horario abierto", delivery="Híbrido", language="Español / Inglés", credential=AHA,
          assessment=EXAM_76[0], passing=EXAM_76[1], max_p="20 (grupos más grandes bajo solicitud)", min_p="5",
          prereq="Ser alumno activo o haber concluido el Diplomado en Atención Médica Prehospitalaria",
          education="Bachillerato terminado",
          materials="Plataforma de contenidos y maniquíes con retroalimentación en el centro")),
    ("HP046", "HP", "Primeros Auxilios Psicológicos", "Psychological First Aid", "8", "1 día", "755",
     "Proporciona herramientas para la atención emocional inmediata a personas en crisis durante…",
     "Personas que no son profesionales de la salud",
     dict(prereq=OPEN_PUBLIC, age="15", max_p="20 (grupos más grandes bajo solicitud)", min_p="5",
          credential=CRM + " / DC-3 de competencias laborales de la STPS para empleados de empresas")),
    ("HP047", "HP", "Estándar de Competencia en Atención Prehospitalaria (CONOCER)",
     "Labour Competency Standard in Prehospital Care", "1 h 45 min en gabinete + 5 h en campo", "2 días", "3200",
     "Es el documento oficial que sirve para evaluar y certificar la competencia para ejecutar atención…",
     "Profesionales de la salud",
     dict(category="Certification", track="Certificación de profesionales de la salud", days="2 días",
          schedule="2 sesiones", language=SPA, assessment=EXAM_76[0], passing="98%",
          max_p="20 (grupos más grandes bajo solicitud)", min_p="5",
          prereq="Haber concluido satisfactoriamente el Diplomado en Atención Médica Prehospitalaria",
          education="Bachillerato terminado",
          materials="Material y equipo de simulación para el examen de certificación",
          credential="Certificado de Competencia Laboral en Atención Prehospitalaria (CONOCER)")),
]

# Compliance flags from the client's data guide — carried into the cards so the
# agent knows where it must not improvise. See core/generation/check_flags.
FLAGS = {
    "HP014": "shared_description:HP015",
    "HP015": "shared_description:HP014",
    "GP001": "shared_objectives:GP008",
    "GP008": "shared_objectives:GP001",
    "GP009": "package_pricing;branch_capacity",
    "HP041": "package_pricing;branch_capacity",
    "HP042": "enrollment_plus_monthly_pricing",
    "HP043": "enrollment_plus_monthly_pricing",
    "HP044": "enrollment_plus_monthly_pricing",
}

COLUMNS = [
    "Course_ID", "Course_Name_ES", "Course_Name_EN", "Program_Track", "Course_Category",
    "Short_Description", "Target_Audience", "Prerequisites_Description", "Minimum_Age",
    "Required_Education_Level", "Total_Contact_Hours", "Duration_Days", "Duration_Calendar_Span",
    "Schedule_Format", "Delivery_Mode", "Language_of_Instruction", "Materials_Included",
    "Assessment_Method", "Passing_Score", "Max_Participants_Per_Class", "Min_Participants_To_Run",
    "What_They_Get", "Price_Per_Person_MXN", "Compliance_Flags",
]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        for course_id, family, name_es, name_en, hours, span, price, desc, audience, overrides in ROWS:
            if course_id in seen:
                raise ValueError(f"Duplicate course id: {course_id}")
            seen.add(course_id)
            f = {**FAMILIES[family], **overrides}
            writer.writerow([
                course_id, name_es, name_en, f["track"], f["category"], desc, audience,
                f["prereq"], f["age"], f["education"], hours, f["days"], span, f["schedule"],
                f["delivery"], f["language"], f["materials"], f["assessment"], f["passing"],
                f["max_p"], f["min_p"], f["credential"], price, FLAGS.get(course_id, ""),
            ])
    print(f"Wrote {len(ROWS)} courses to {OUT}")


if __name__ == "__main__":
    main()
