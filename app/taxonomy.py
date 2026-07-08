from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.models import ClassifiedCandidate
from app.relevance import ALWAYS_RELEVANT_AUTHORITY_TERMS
from app.text import normalized, words

# Catálogo de órganos emisores: (nombre canónico, rama de gobierno, nombre
# corto, alias normalizados). El primer alias que aparezca en la autoridad o
# el título oficial determina el órgano; el orden del catálogo resuelve empates.
ORGAN_CATALOG: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "Secretaría de Hacienda y Crédito Público",
        "Ejecutivo federal",
        "SHCP",
        ("secretaria de hacienda", "shcp", "hacienda y credito publico"),
    ),
    (
        "Servicio de Administración Tributaria",
        "Ejecutivo federal",
        "SAT",
        ("servicio de administracion tributaria", "sat"),
    ),
    (
        "Agencia Nacional de Aduanas de México",
        "Ejecutivo federal",
        "ANAM",
        ("agencia nacional de aduanas", "anam"),
    ),
    (
        "Secretaría de Economía",
        "Ejecutivo federal",
        "SE",
        ("secretaria de economia", "snice", "comision de comercio exterior"),
    ),
    (
        "Secretaría de Relaciones Exteriores",
        "Ejecutivo federal",
        "SRE",
        ("secretaria de relaciones exteriores", "sre"),
    ),
    (
        "Secretaría de Gobernación",
        "Ejecutivo federal",
        "SEGOB",
        ("secretaria de gobernacion", "segob"),
    ),
    (
        "Instituto Mexicano de la Propiedad Industrial",
        "Organismo descentralizado",
        "IMPI",
        ("instituto mexicano de la propiedad industrial", "impi"),
    ),
    (
        "Instituto Nacional del Derecho de Autor",
        "Órgano administrativo desconcentrado",
        "INDAUTOR",
        ("instituto nacional del derecho de autor", "indautor"),
    ),
    (
        "Procuraduría Federal del Consumidor",
        "Organismo descentralizado",
        "PROFECO",
        ("procuraduria federal del consumidor", "profeco"),
    ),
    (
        "Comisión Nacional para la Protección y Defensa de los Usuarios de Servicios Financieros",
        "Organismo descentralizado",
        "CONDUSEF",
        (
            "comision nacional para la proteccion y defensa de los usuarios",
            "condusef",
        ),
    ),
    (
        "Comisión Nacional Bancaria y de Valores",
        "Órgano administrativo desconcentrado",
        "CNBV",
        ("comision nacional bancaria y de valores", "cnbv"),
    ),
    (
        "Comisión Federal de Competencia Económica",
        "Organismo autónomo",
        "COFECE",
        ("comision federal de competencia economica", "cofece"),
    ),
    (
        "Instituto Nacional de Transparencia, Acceso a la Información y Protección de Datos",
        "Organismo autónomo",
        "INAI",
        ("instituto nacional de transparencia", "inai"),
    ),
    (
        "Tribunal Federal de Justicia Administrativa",
        "Órgano jurisdiccional",
        "TFJA",
        ("tribunal federal de justicia administrativa", "tfja"),
    ),
    (
        "Suprema Corte de Justicia de la Nación",
        "Poder Judicial",
        "SCJN",
        ("suprema corte", "scjn"),
    ),
    (
        "Cámara de Diputados",
        "Legislativo federal",
        "Diputados",
        ("camara de diputados", "gaceta parlamentaria"),
    ),
    (
        "Senado de la República",
        "Legislativo federal",
        "Senado",
        ("senado de la republica", "camara de senadores"),
    ),
    (
        "Procuraduría de la Defensa del Contribuyente",
        "Organismo descentralizado",
        "PRODECON",
        ("procuraduria de la defensa del contribuyente", "prodecon"),
    ),
    (
        "Banco de México",
        "Organismo autónomo",
        "Banxico",
        ("banco de mexico", "banxico"),
    ),
    (
        "Instituto Mexicano del Seguro Social",
        "Organismo descentralizado",
        "IMSS",
        ("instituto mexicano del seguro social", "imss"),
    ),
    (
        "Instituto de Seguridad y Servicios Sociales de los Trabajadores del Estado",
        "Organismo descentralizado",
        "ISSSTE",
        (
            "instituto de seguridad y servicios sociales de los trabajadores del estado",
            "issste",
        ),
    ),
    (
        "Instituto Nacional de los Pueblos Indígenas",
        "Organismo descentralizado",
        "INPI",
        ("instituto nacional de los pueblos indigenas", "inpi"),
    ),
    (
        "Fiscalía General de la República",
        "Organismo autónomo",
        "FGR",
        ("fiscalia general de la republica", "fgr"),
    ),
    (
        "Secretaría de Marina",
        "Ejecutivo federal",
        "Semar",
        ("secretaria de marina", "semar"),
    ),
    (
        "Presidencia de la República",
        "Ejecutivo federal",
        "Presidencia",
        ("presidencia de la republica", "poder ejecutivo", "oficina de la presidencia"),
    ),
    # Organismos internacionales (fuentes fase internacional).
    (
        "Organización de las Naciones Unidas",
        "Organismo internacional",
        "ONU",
        ("naciones unidas", "onu", "un news"),
    ),
    (
        "Oficina del Representante Comercial de EEUU",
        "Gobierno de EEUU",
        "USTR",
        ("ustr", "united states trade representative"),
    ),
    (
        "Departamento de Comercio de EEUU",
        "Gobierno de EEUU",
        "ITA",
        ("trade.gov", "department of commerce", "international trade administration"),
    ),
    (
        "Centro Internacional de Arreglo de Diferencias relativas a Inversiones",
        "Organismo internacional",
        "CIADI",
        ("ciadi", "centro internacional de arreglo de diferencias", "icsid"),
    ),
    (
        "Banco Mundial",
        "Organismo internacional",
        "Banco Mundial",
        ("banco mundial", "world bank"),
    ),
    (
        "Fondo Monetario Internacional",
        "Organismo internacional",
        "FMI",
        ("fondo monetario internacional", "fmi", "imf"),
    ),
    (
        "Organización Mundial del Comercio",
        "Organismo internacional",
        "OMC",
        ("organizacion mundial del comercio", "omc", "wto"),
    ),
    (
        "Organización para la Cooperación y el Desarrollo Económicos",
        "Organismo internacional",
        "OCDE",
        ("organizacion para la cooperacion", "ocde", "oecd"),
    ),
    (
        "Corte Penal Internacional",
        "Organismo internacional",
        "CPI",
        ("corte penal internacional", "international criminal court"),
    ),
    (
        "Corte Internacional de Justicia",
        "Organismo internacional",
        "CIJ",
        ("corte internacional de justicia", "international court of justice"),
    ),
    (
        "Secretariado del T-MEC",
        "Organismo internacional",
        "T-MEC",
        ("secretariado del t-mec", "secretariado t-mec"),
    ),
)

# Origen por fuente del recolector: (jurisdicción, país u organismo).
SOURCE_ORIGIN: dict[str, tuple[str, str]] = {
    "ONU Noticias": ("internacional", "ONU"),
    "USTR": ("internacional", "EEUU"),
    "Trade.gov": ("internacional", "EEUU"),
    "CIADI": ("internacional", "CIADI"),
    "Banco Mundial": ("internacional", "Banco Mundial"),
    "CPI": ("internacional", "CPI"),
    "CIJ": ("internacional", "CIJ"),
    "OMC": ("internacional", "OMC"),
    "Secretariado T-MEC": ("internacional", "T-MEC"),
}
DEFAULT_ORIGIN = ("nacional", "México")

# Materia primaria por defecto según la fuente internacional del recolector.
SOURCE_DEFAULT_PRIMARY: dict[str, str] = {
    "CIADI": "Comercio internacional",
    "Banco Mundial": "Comercio internacional",
    "OMC": "Comercio internacional",
    "CIJ": "Comercio internacional",
    "CPI": "Penal",
    "Secretariado T-MEC": "Comercio internacional",
}

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
    primary_categories: tuple[str, ...]
    topic_tags: tuple[str, ...]
    subtopic_tags: tuple[str, ...]
    importance: int


# Ramas de gobierno que corresponden a la Administración descentralizada.
DESCENTRALIZED_BRANCH_MARKERS = ("descentralizado", "desconcentrado", "autonomo")


def _alias_matches(haystack: str, alias: str) -> bool:
    """Busca el alias con frontera de palabra: los alias cortos ("onu", "imf",
    "fmi") no deben dispararse dentro de otras siglas ("cONUee", "hIMFg")."""
    return re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", haystack) is not None


def classify_organ(source: str, authority: str, official_title: str) -> tuple[str, str]:
    """Resuelve (órgano canónico, rama) buscando alias primero en la autoridad
    declarada y después en el título oficial."""
    for haystack in (normalized(authority), normalized(official_title)):
        if not haystack:
            continue
        for canonical, branch, _short, aliases in ORGAN_CATALOG:
            if any(_alias_matches(haystack, alias) for alias in aliases):
                return canonical, branch

    jurisdiction, _ = SOURCE_ORIGIN.get(source, DEFAULT_ORIGIN)
    if authority.strip():
        branch = (
            "Organismo internacional" if jurisdiction == "internacional" else "Ejecutivo federal"
        )
        return authority.strip(), branch
    return "Autoridad no identificada", "Sin clasificar"


def short_organ_name(source: str, authority: str, official_title: str) -> str:
    """Nombre corto del órgano emisor (SHCP, SAT, IMPI, ...). Cae al primer
    token razonable de la autoridad declarada, o al nombre de la fuente."""
    for haystack in (normalized(authority), normalized(official_title)):
        if not haystack:
            continue
        for _canonical, _branch, short, aliases in ORGAN_CATALOG:
            if any(_alias_matches(haystack, alias) for alias in aliases):
                return short

    authority_tokens = words(authority)
    if authority_tokens:
        return authority_tokens[0]
    return source.strip() or "la autoridad"


def primary_categories(
    fine_categories: tuple[str, ...],
    government_branch: str,
    authority: str,
    source: str,
) -> tuple[str, ...]:
    """Colapsa las categorías finas a las materias primarias del dueño.

    Las categorías finas se conservan aparte como etiquetas temáticas; aquí
    sólo se decide bajo qué materia primaria se agrupa la publicación.
    """
    branch = normalized(government_branch)
    is_descentralized = any(marker in branch for marker in DESCENTRALIZED_BRANCH_MARKERS)
    administration = (
        "Administración descentralizada" if is_descentralized else "Administración centralizada"
    )

    primaries: list[str] = []
    for category in fine_categories:
        if category in ("Fiscal", "Contencioso administrativo fiscal"):
            primaries.append("Fiscal")
        elif category in ("Aduanero", "Comercio exterior"):
            primaries.append("Comercio exterior")
        elif category == "Comercio internacional":
            primaries.append("Comercio internacional")
        elif category == "Penal":
            primaries.append("Penal")
        elif category == "Anti-lavado":
            primaries.append("Anti-lavado")
        elif category in (
            "Derecho administrativo",
            "Nombramientos federales",
            "Contencioso administrativo",
        ):
            primaries.append("Administración centralizada")
        elif category in ("Propiedad intelectual", "Normalización"):
            primaries.append(administration)
        elif category == "Iniciativa":
            primaries.append("Proceso legislativo")

    normalized_authority = normalized(authority)
    if any(term in normalized_authority for term in ALWAYS_RELEVANT_AUTHORITY_TERMS):
        primaries.append("Administración centralizada")

    default_primary = SOURCE_DEFAULT_PRIMARY.get(source)
    if default_primary:
        primaries.append(default_primary)

    if not primaries:
        primaries.append("Administración centralizada")

    return tuple(dict.fromkeys(primaries))


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

    primaries = primary_categories(
        item.categories,
        branch,
        candidate.authority,
        candidate.source,
    )

    published: date = candidate.published_at
    return Taxonomy(
        issuing_body=issuing_body,
        government_branch=branch,
        jurisdiction=jurisdiction,
        country_or_org=country_or_org,
        published_year=published.year,
        published_month=published.month,
        published_day=published.day,
        primary_categories=primaries,
        topic_tags=tuple(dict.fromkeys(topic_tags)),
        subtopic_tags=subtopic_tags,
        importance=importance_from_score(item.relevance_score),
    )
