from __future__ import annotations

import re

from app.models import Candidate, ClassifiedCandidate
from app.text import normalized

CATEGORY_TERMS: dict[str, tuple[str, ...]] = {
    "Fiscal": (
        "impuesto",
        "contribuyente",
        "codigo fiscal",
        "renta",
        "iva",
        "isr",
        "ieps",
        "sat",
        "prodecon",
        "credito fiscal",
        "ejecucion fiscal",
        "procedimiento administrativo de ejecucion",
        "comprobante fiscal",
        "cfdi",
        "tax",
        "taxation",
        "transfer pricing",
    ),
    "Aduanero": (
        "aduana",
        "aduanero",
        "agencia nacional de aduanas",
        "reglas generales de comercio exterior",
        "pedimento",
        "recinto fiscal",
        "importador",
        "exportador",
        "customs",
        "customs duties",
    ),
    "Comercio exterior": (
        "comercio exterior",
        "importacion",
        "exportacion",
        "arancel",
        "tarifa de la ley de los impuestos generales",
        "ligie",
        "tigie",
        "cuota compensatoria",
        "antidumping",
        "tratado de libre comercio",
        "cupo",
        "regla de origen",
        "immex",
        "prosec",
        "tariff",
        "tariffs",
        "trade agreement",
        "usmca",
        "free trade",
        "trade remedy",
        "antidumping duty",
        "section 301",
        "export control",
        "sanction",
        "sanctions",
        "wto",
    ),
    "Propiedad intelectual": (
        "propiedad intelectual",
        "propiedad industrial",
        "impi",
        "patente",
        "marca",
        "derecho de autor",
        "pirateria",
        "signos distintivos",
        "modelo de utilidad",
        "secreto industrial",
        "patent",
        "trademark",
        "copyright",
        "intellectual property",
    ),
    "Normalización": (
        "norma oficial mexicana",
        "proy-nom",
        "nom-",
        "normalizacion",
        "infraestructura de la calidad",
        "evaluacion de la conformidad",
    ),
    "Iniciativa": (
        "iniciativa",
        "proyecto de decreto",
        "reforma",
        "adiciona",
        "deroga",
        "dictamen",
        "minuta",
    ),
    "Penal": (
        "delito",
        "penal",
        "fiscalia",
        "fgr",
        "defraudacion fiscal",
        "contrabando",
        "extincion de dominio",
        "prision preventiva",
        "codigo nacional de procedimientos penales",
        "orden de aprehension",
        "war crimes",
        "crimes against humanity",
        "prosecutor",
        "arrest warrant",
    ),
    "Anti-lavado": (
        "lavado de dinero",
        "lfpiorpi",
        "actividades vulnerables",
        "uif",
        "unidad de inteligencia financiera",
        "gafi",
        "fatf",
        "beneficiario controlador",
        "operaciones inusuales",
        "money laundering",
        "aml",
        "terrorist financing",
    ),
    "Comercio internacional": (
        "t-mec",
        "usmca",
        "omc",
        "wto",
        "ocde",
        "oecd",
        "arbitraje de inversion",
        "investment arbitration",
        "icsid",
        "ciadi",
        "tratado bilateral de inversion",
        "panel binacional",
        "dispute settlement",
        "world bank",
        "banco mundial",
        "imf",
        "fmi",
        "aranceles reciprocos",
        "global tariff",
    ),
}

# Autoridades cuyo material siempre es relevante para el radar, aunque el
# clasificador no le asigne materia ni score alto (enrich les da como materia
# primaria "Administración centralizada").
ALWAYS_RELEVANT_AUTHORITY_TERMS = (
    "secretaria de hacienda",
    "shcp",
    "hacienda y credito publico",
    "secretaria de economia",
)

ADMINISTRATIVE_LAW_TERMS = (
    "ley federal de procedimiento administrativo",
    "lfpa",
    "ley organica de la administracion publica federal",
    "loapf",
    "reglamento interior",
    "estatuto organico",
    "manual de organizacion",
    "estructura organica",
    "administracion publica federal",
    "servicio profesional de carrera",
    "delegan facultades",
    "delegacion de facultades",
    "facultades delegadas",
    "acuerdo delegatorio",
    "adscripcion de unidades administrativas",
    "unidades administrativas",
    "organos administrativos desconcentrados",
    "sectorizacion",
    "desincorporacion",
    "extincion de organismo",
    "creacion de organismo",
)

APPOINTMENT_ACTION_TERMS = (
    "nombramiento",
    "nombramientos",
    "nombra",
    "nombro",
    "nombrado",
    "nombrada",
    "designacion",
    "designa",
    "designo",
    "designado",
    "designada",
    "designados",
    "designadas",
    "ratificacion",
    "ratifica",
    "remocion",
    "renuncia",
    "suplencia",
    "encargado de despacho",
    "encargada de despacho",
    "toma de protesta",
)

FEDERAL_POSITION_TERMS = (
    "secretario de estado",
    "secretaria de estado",
    "subsecretario",
    "subsecretaria",
    "titular de la secretaria",
    "titular del organo",
    "titular de la unidad",
    "titular de la jefatura",
    "titular de jefatura",
    "titular del instituto",
    "titular del organismo",
    "director general",
    "directora general",
    "director de area",
    "directora de area",
    "subdirector",
    "subdirectora",
    "jefe de unidad",
    "jefa de unidad",
    "jefe de departamento",
    "jefa de departamento",
    "coordinador general",
    "coordinadora general",
    "oficial mayor",
    "comisionado",
    "comisionada",
    "presidente del organismo",
    "presidenta del organismo",
    "secretario tecnico",
    "secretaria tecnica",
    "vocal ejecutivo",
    "vocal ejecutiva",
    "embajador",
    "embajadora",
    "consul general",
)

APPOINTMENT_EXPLICIT_TERMS = (
    "nombramientos presidenciales",
    "nuevo titular",
    "nueva titular",
    "nuevo director general",
    "nueva directora general",
)

CONTENTIOUS_EXPLICIT_TERMS = (
    "ley federal de procedimiento contencioso administrativo",
    "lfpca",
    "juicio contencioso administrativo",
    "juicio de nulidad",
    "demanda de nulidad",
    "nulidad lisa y llana",
    "nulidad para efectos",
    "recurso de reclamacion",
    "incidente de suspension",
    "medida cautelar",
)

CONTENTIOUS_PROCESS_TERMS = (
    "sentencia definitiva",
    "cumplimiento de sentencia",
    "nulidad de la resolucion",
    "plazos procesales",
    "terminos y plazos procesales",
    "actividades jurisdiccionales",
    "procedimiento jurisdiccional",
    "medio de defensa",
)

ADMINISTRATIVE_COURT_TERMS = (
    "tribunal federal de justicia administrativa",
    "tfja",
    "sala regional",
    "sala superior",
    "sala especializada",
)

FISCAL_CONTENTIOUS_TERMS = (
    "juicio de resolucion exclusiva de fondo",
    "juicio contencioso administrativo federal en materia fiscal",
    "sala especializada en materia fiscal",
    "nulidad de credito fiscal",
    "impugnacion de credito fiscal",
)

HIGH_VALUE_TERMS = (
    "codigo fiscal",
    "ley aduanera",
    "reglas generales de comercio exterior",
    "ley del impuesto",
    "tarifa de la ley de los impuestos generales",
    "propiedad industrial",
    "cuota compensatoria",
    "ley federal de procedimiento administrativo",
    "ley organica de la administracion publica federal",
    "ley federal de procedimiento contencioso administrativo",
    "nombramiento",
    "designacion",
    "iniciativa",
    "proyecto de decreto",
    "usmca",
    "tariff",
    "trade agreement",
)


def classify(candidate: Candidate) -> ClassifiedCandidate:
    text = normalized(
        " ".join(
            (
                candidate.official_title,
                candidate.description,
                candidate.document_type,
            )
        )
    )
    categories: list[str] = []
    matches: list[str] = []

    for category, terms in CATEGORY_TERMS.items():
        category_matches = _matching_terms(text, terms)
        if category_matches:
            categories.append(category)
            matches.extend(category_matches)

    administrative_matches = _matching_terms(text, ADMINISTRATIVE_LAW_TERMS)
    appointment_actions = _matching_terms(text, APPOINTMENT_ACTION_TERMS)
    appointment_positions = _matching_terms(text, FEDERAL_POSITION_TERMS)
    explicit_appointments = _matching_terms(text, APPOINTMENT_EXPLICIT_TERMS)
    is_appointment = bool(explicit_appointments) or _has_term_proximity(
        text,
        APPOINTMENT_ACTION_TERMS,
        FEDERAL_POSITION_TERMS,
    )

    if administrative_matches or is_appointment:
        categories.append("Derecho administrativo")
        matches.extend(administrative_matches)
        matches.extend(appointment_actions)
        matches.extend(appointment_positions)
        matches.extend(explicit_appointments)
    if is_appointment:
        categories.append("Nombramientos federales")

    explicit_contentious = _matching_terms(text, CONTENTIOUS_EXPLICIT_TERMS)
    contentious_process = _matching_terms(text, CONTENTIOUS_PROCESS_TERMS)
    court_context = _matching_terms(text, ADMINISTRATIVE_COURT_TERMS)
    is_contentious = bool(explicit_contentious or (contentious_process and court_context))
    if is_contentious:
        categories.append("Contencioso administrativo")
        matches.extend(explicit_contentious)
        matches.extend(contentious_process)
        matches.extend(court_context)

    fiscal_contentious = _matching_terms(text, FISCAL_CONTENTIOUS_TERMS)
    if fiscal_contentious or (is_contentious and "Fiscal" in categories):
        categories.append("Contencioso administrativo fiscal")
        matches.extend(fiscal_contentious)

    unique_categories = tuple(dict.fromkeys(categories))
    unique_matches = tuple(dict.fromkeys(matches))
    score = min(len(unique_matches), 4)
    score += sum(1 for term in HIGH_VALUE_TERMS if _contains_term(text, term))
    score += 1 if candidate.source in {"DOF", "Senado", "Diputados"} else 0

    return ClassifiedCandidate(
        candidate=candidate,
        categories=unique_categories,
        relevance_score=score,
        matched_terms=unique_matches,
    )


def _matching_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if _contains_term(text, term)]


def _has_term_proximity(
    text: str,
    left_terms: tuple[str, ...],
    right_terms: tuple[str, ...],
    maximum_distance: int = 160,
) -> bool:
    left_spans = _term_spans(text, left_terms)
    right_spans = _term_spans(text, right_terms)
    for left_start, left_end in left_spans:
        for right_start, right_end in right_spans:
            distance = min(
                abs(left_start - right_end),
                abs(right_start - left_end),
            )
            between_start = min(left_end, right_end)
            between_end = max(left_start, right_start)
            between = text[between_start:between_end]
            if distance <= maximum_distance and not re.search(r"[.!?;]", between):
                return True
    return False


def _term_spans(text: str, terms: tuple[str, ...]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for term in terms:
        normalized_term = normalized(term)
        pattern = (
            rf"(?<!\w){re.escape(normalized_term)}(?!\w)"
            if normalized_term.replace(" ", "").isalnum()
            else re.escape(normalized_term)
        )
        spans.extend(match.span() for match in re.finditer(pattern, text))
    return spans


def _contains_term(text: str, term: str) -> bool:
    normalized_term = normalized(term)
    if normalized_term.replace(" ", "").isalnum():
        return (
            re.search(
                rf"(?<!\w){re.escape(normalized_term)}(?!\w)",
                text,
            )
            is not None
        )
    return normalized_term in text


def is_relevant(item: ClassifiedCandidate, minimum_score: int) -> bool:
    authority = normalized(item.candidate.authority)
    if any(term in authority for term in ALWAYS_RELEVANT_AUTHORITY_TERMS):
        return True
    substantive_categories = set(item.categories) - {"Iniciativa"}
    return bool(substantive_categories) and item.relevance_score >= minimum_score
