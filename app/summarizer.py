from __future__ import annotations

import re
from dataclasses import dataclass

from app.markdown import build_card_body
from app.models import ClassifiedCandidate
from app.text import concise_title, exactly_30_words, normalized, words


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
        categories = ", ".join(item.categories)
        summary_base = (
            f"{base}. Publicación oficial de {candidate.authority}, relacionada con {categories}. "
            "Consulta la fuente íntegra para confirmar su alcance, fechas de vigencia, "
            "obligaciones, requisitos, excepciones, autoridades responsables, procedimientos "
            "y efectos jurídicos aplicables."
        )
        document_type = candidate.document_type.strip() or "Publicación oficial"
        authority = candidate.authority.strip() or "autoridad federal"
        what_published = f"{document_type} de {authority}."
        substance = (
            f"{base}. Materias: {categories or 'sin clasificar'}. Resumen extractivo "
            "pendiente de revisión editorial; consulta la fuente oficial para el "
            "alcance completo."
        )
        return Summary(
            title=concise_title(title_base),
            summary=exactly_30_words(summary_base),
            card_body=build_card_body(
                what_published=what_published,
                substance=substance,
                source_url=candidate.url,
            ),
            ai_generated=False,
        )


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
