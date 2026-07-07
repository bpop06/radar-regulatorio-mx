from __future__ import annotations

import re
from dataclasses import dataclass

from app.markdown import build_card_body
from app.models import ClassifiedCandidate
from app.taxonomy import short_organ_name
from app.text import bounded_summary, concise_title, normalized, words

# El tipo base de acto se deriva del document_type cortando en el primer dígito
# (así "Oficio 500-05-2026-1" queda en "Oficio", sin arrastrar el número).
_ACT_TYPE_BASE_RE = re.compile(r"^[^\d]*")


@dataclass(frozen=True)
class Summary:
    title: str
    summary: str
    card_body: str
    ai_generated: bool


class Summarizer:
    """Resumen extractivo determinista.

    La capa editorial (título de noticia, resumen razonado y cuerpo de la
    ficha) NO se genera aquí: la produce la rutina diaria de Claude en la
    nube mediante `app.cli apply-editorial`, que marca `ai_generated=true`.
    Este generador garantiza que toda publicación salga completa y válida
    aunque la rutina editorial aún no haya corrido.
    """

    def summarize(self, item: ClassifiedCandidate) -> Summary:
        candidate = item.candidate
        base = (candidate.description or candidate.official_title).rstrip(" .;:")
        title_base = _fallback_title_base(candidate.official_title, candidate.description)
        authority = candidate.authority.strip() or "autoridad federal"
        categories = ", ".join(item.categories) or "materias reguladas por el radar"

        lead = f"{base}. " if base else ""
        summary_base = (
            f"{lead}Publicación oficial de {authority}, relacionada con {categories}. "
            "Consulta la fuente íntegra para confirmar su alcance, fechas de vigencia, "
            "obligaciones, requisitos, excepciones, autoridades responsables, procedimientos "
            "y efectos jurídicos aplicables."
        )
        # "Qué se publicó" sin número de acto ni nombre completo del órgano.
        act_type = _base_act_type(candidate.document_type)
        organ_short = short_organ_name(
            candidate.source, candidate.authority, candidate.official_title
        )
        what_published = f"{act_type} de {organ_short}."
        substance = (
            f"{lead}Materias: {categories}. Resumen extractivo "
            "pendiente de revisión editorial; consulta la fuente oficial para el "
            "alcance completo."
        )
        return Summary(
            title=concise_title(title_base),
            summary=bounded_summary(summary_base),
            card_body=build_card_body(
                what_published=what_published,
                substance=substance,
                source_url=candidate.url,
            ),
            ai_generated=False,
        )


def _base_act_type(document_type: str) -> str:
    """Tipo base del acto sin número ni clave: corta el document_type en el
    primer dígito y limpia separadores sobrantes."""
    match = _ACT_TYPE_BASE_RE.match(document_type.strip())
    base = (match.group(0) if match else "").strip(" -/.,:;")
    return base or "Publicación oficial"


def _fallback_title_base(official_title: str, description: str) -> str:
    title_base = description if len(words(description)) >= 5 else official_title

    generic_prefixes = (
        "proyecto de decreto",
        "iniciativa con proyecto",
        "punto de acuerdo",
    )
    if normalized(title_base).startswith(generic_prefixes):
        title_base = re.sub(
            r"^(proyecto de decreto|iniciativa con proyecto(?: de decreto)?|punto de acuerdo)"
            r"\s+(?:por el que|que)?\s*",
            "",
            title_base,
            flags=re.IGNORECASE,
        )
    return title_base
