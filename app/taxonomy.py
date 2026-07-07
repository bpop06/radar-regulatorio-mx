from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.models import ClassifiedCandidate
from app.text import normalized

# Catálogo de órganos emisores: (nombre canónico, rama de gobierno, alias
# normalizados). El primer alias que aparezca en la autoridad o el título
# oficial determina el órgano; el orden del catálogo resuelve empates.
ORGAN_CATALOG: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "Secretaría de Hacienda y Crédito Público",
        "Ejecutivo federal",
        ("secretaria de hacienda", "shcp", "hacienda y credito publico"),
    ),
    (
        "Servicio de Administración Tributaria",
        "Ejecutivo federal",
        ("servicio de administracion tributaria", "sat"),
    ),
    (
        "Agencia Nacional de Aduanas de México",
        "Ejecutivo federal",
        ("agencia nacional de aduanas", "anam"),
    ),
    (
        "Secretaría de Economía",
        "Ejecutivo federal",
        ("secretaria de economia", "snice", "comision de comercio exterior"),
    ),
    (
        "Secretaría de Relaciones Exteriores",
        "Ejecutivo federal",
        ("secretaria de relaciones exteriores", "sre"),
    ),
    (
        "Secretaría de Gobernación",
        "Ejecutivo federal",
        ("secretaria de gobernacion", "segob"),
    ),
    (
        "Instituto Mexicano de la Propiedad Industrial",
        "Organismo descentralizado",
        ("instituto mexicano de la propiedad industrial", "impi"),
    ),
    (
        "Tribunal Federal de Justicia Administrativa",
        "Órgano jurisdiccional",
        ("tribunal federal de justicia administrativa", "tfja"),
    ),
    (
        "Suprema Corte de Justicia de la Nación",
        "Poder Judicial",
        ("suprema corte", "scjn"),
    ),
    (
        "Cámara de Diputados",
        "Legislativo federal",
        ("camara de diputados", "gaceta parlamentaria"),
    ),
    (
        "Senado de la República",
        "Legislativo federal",
        ("senado de la republica", "camara de senadores"),
    ),
    (
        "Procuraduría de la Defensa del Contribuyente",
        "Organismo autónomo",
        ("procuraduria de la defensa del contribuyente", "prodecon"),
    ),
    (
        "Banco de México",
        "Organismo autónomo",
        ("banco de mexico", "banxico"),
    ),
    (
        "Presidencia de la República",
        "Ejecutivo federal",
        ("presidencia de la republica", "poder ejecutivo", "oficina de la presidencia"),
    ),
    # Organismos internacionales (fuentes fase internacional).
    (
        "Organización de las Naciones Unidas",
        "Organismo internacional",
        ("naciones unidas", "onu", "un news"),
    ),
    (
        "Oficina del Representante Comercial de EEUU",
        "Gobierno de EEUU",
        ("ustr", "united states trade representative"),
    ),
    (
        "Departamento de Comercio de EEUU",
        "Gobierno de EEUU",
        ("trade.gov", "department of commerce", "international trade administration"),
    ),
)

# Origen por fuente del recolector: (jurisdicción, país u organismo).
SOURCE_ORIGIN: dict[str, tuple[str, str]] = {
    "ONU Noticias": ("internacional", "ONU"),
    "USTR": ("internacional", "EEUU"),
    "Trade.gov": ("internacional", "EEUU"),
}
DEFAULT_ORIGIN = ("nacional", "México")

# Etiquetas temáticas derivadas del tipo documental (además de las materias).
DOCUMENT_TYPE_TAGS: tuple[tuple[str, str], ...] = (
    ("decreto", "Decreto"),
    ("acuerdo", "Acuerdo"),
    ("resolucion", "Resolución"),
    ("aviso", "Aviso"),
    ("convocatoria", "Convocatoria"),
    ("iniciativa", "Iniciativa"),
    ("sentencia", "Sentencia"),
    ("oficio", "Oficio"),
    ("norma", "Norma"),
    ("reglamento", "Reglamento"),
    ("nombramiento", "Nombramiento"),
    ("circular", "Circular"),
)

MAX_SUBTOPIC_TAGS = 6


@dataclass(frozen=True)
class Taxonomy:
    issuing_body: str
    government_branch: str
    jurisdiction: str
    country_or_org: str
    published_year: int
    published_month: int
    published_day: int
    topic_tags: tuple[str, ...]
    subtopic_tags: tuple[str, ...]
    importance: int


def classify_organ(source: str, authority: str, official_title: str) -> tuple[str, str]:
    """Resuelve (órgano canónico, rama) buscando alias primero en la autoridad
    declarada y después en el título oficial."""
    for haystack in (normalized(authority), normalized(official_title)):
        if not haystack:
            continue
        for canonical, branch, aliases in ORGAN_CATALOG:
            if any(alias in haystack for alias in aliases):
                return canonical, branch

    jurisdiction, _ = SOURCE_ORIGIN.get(source, DEFAULT_ORIGIN)
    if authority.strip():
        branch = (
            "Organismo internacional" if jurisdiction == "internacional" else "Ejecutivo federal"
        )
        return authority.strip(), branch
    return "Autoridad no identificada", "Sin clasificar"


def importance_from_score(relevance_score: int) -> int:
    """Colapsa el puntaje abierto de relevancia a una escala editorial 1-5."""
    if relevance_score >= 8:
        return 5
    if relevance_score >= 6:
        return 4
    if relevance_score >= 4:
        return 3
    if relevance_score >= 2:
        return 2
    return 1


def enrich(item: ClassifiedCandidate) -> Taxonomy:
    candidate = item.candidate
    issuing_body, branch = classify_organ(
        candidate.source, candidate.authority, candidate.official_title
    )
    jurisdiction, country_or_org = SOURCE_ORIGIN.get(candidate.source, DEFAULT_ORIGIN)

    topic_tags: list[str] = list(item.categories)
    doc_text = normalized(f"{candidate.document_type} {candidate.official_title}")
    for needle, tag in DOCUMENT_TYPE_TAGS:
        if needle in doc_text and tag not in topic_tags:
            topic_tags.append(tag)

    subtopic_tags = tuple(
        dict.fromkeys(term.strip() for term in item.matched_terms if term.strip())
    )[:MAX_SUBTOPIC_TAGS]

    published: date = candidate.published_at
    return Taxonomy(
        issuing_body=issuing_body,
        government_branch=branch,
        jurisdiction=jurisdiction,
        country_or_org=country_or_org,
        published_year=published.year,
        published_month=published.month,
        published_day=published.day,
        topic_tags=tuple(dict.fromkeys(topic_tags)),
        subtopic_tags=subtopic_tags,
        importance=importance_from_score(item.relevance_score),
    )
