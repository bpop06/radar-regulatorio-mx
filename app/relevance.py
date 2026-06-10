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
        "comprobante fiscal",
        "cfdi",
        "lavado de dinero",
        "actividades vulnerables",
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
    ),
    "Procesal fiscal": (
        "juicio contencioso",
        "tribunal federal de justicia administrativa",
        "recurso de revocacion",
        "amparo fiscal",
        "medio de defensa",
        "procedimiento administrativo de ejecucion",
        "garantia del interes fiscal",
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
}

HIGH_VALUE_TERMS = (
    "codigo fiscal",
    "ley aduanera",
    "reglas generales de comercio exterior",
    "ley del impuesto",
    "tarifa de la ley de los impuestos generales",
    "propiedad industrial",
    "cuota compensatoria",
    "iniciativa",
    "proyecto de decreto",
)


def classify(candidate: Candidate) -> ClassifiedCandidate:
    text = normalized(candidate.searchable_text)
    categories: list[str] = []
    matches: list[str] = []

    for category, terms in CATEGORY_TERMS.items():
        category_matches = [term for term in terms if _contains_term(text, term)]
        if category_matches:
            categories.append(category)
            matches.extend(category_matches)

    unique_matches = tuple(dict.fromkeys(matches))
    score = min(len(unique_matches), 4)
    score += sum(1 for term in HIGH_VALUE_TERMS if _contains_term(text, term))
    score += 1 if candidate.source in {"DOF", "Senado", "Diputados"} else 0

    return ClassifiedCandidate(
        candidate=candidate,
        categories=tuple(categories),
        relevance_score=score,
        matched_terms=unique_matches,
    )


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
    substantive_categories = set(item.categories) - {"Iniciativa"}
    return bool(substantive_categories) and item.relevance_score >= minimum_score
