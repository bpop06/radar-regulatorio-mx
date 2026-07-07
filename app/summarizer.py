from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass

from app.markdown import build_card_body
from app.models import ClassifiedCandidate
from app.text import concise_title, exactly_30_words, normalized, words

# Esfuerzos aceptados localmente; el valor configurado se intenta primero y,
# si la API lo rechaza, se degrada a "high" con aviso.
KNOWN_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "extra_high")


@dataclass(frozen=True)
class Summary:
    title: str
    summary: str
    card_body: str
    ai_generated: bool


class Summarizer:
    def __init__(self, api_key: str | None, model: str, reasoning_effort: str = "low") -> None:
        self.api_key = api_key
        self.model = model
        self.reasoning_effort = reasoning_effort
        self._client = None

    def summarize(self, item: ClassifiedCandidate) -> Summary:
        if self.api_key:
            try:
                return self._summarize_with_openai(item, self.reasoning_effort)
            except Exception as exc:
                # Un esfuerzo no soportado por la versión de API/modelo no debe
                # degradar hasta el fallback extractivo: se reintenta en high.
                if self.reasoning_effort != "high" and _looks_like_effort_error(exc):
                    print(
                        f"Advertencia: reasoning effort '{self.reasoning_effort}' no soportado; "
                        "se reintenta con 'high'",
                        file=sys.stderr,
                    )
                    try:
                        return self._summarize_with_openai(item, "high")
                    except Exception:
                        pass
        return self._fallback(item)

    def _summarize_with_openai(self, item: ClassifiedCandidate, effort: str) -> Summary:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key)

        candidate = item.candidate
        response = self._client.responses.create(
            model=self.model,
            store=False,
            reasoning={"effort": effort},
            instructions=(
                "Eres editor jurídico de un diario especializado mexicano. "
                "Redacta un titular de noticia con la estructura «órgano + verbo "
                "sustantivo + qué determina/resuelve/decreta/actualiza/deroga», "
                "de máximo 14 palabras, regido por la temática esencial de la "
                "publicación. Nunca inicies el titular con números de oficio, "
                "acuerdo o expediente: esos son metadatos secundarios. "
                "Entrega además: un resumen ejecutivo autosuficiente; una frase "
                "de qué se publicó (tipo de acto y órgano); y la sustancia (qué "
                "cambia, a quién aplica y su efecto), sin inventar fechas, "
                "obligaciones ni efectos."
            ),
            input=(
                f"Fuente: {candidate.source}\n"
                f"Autoridad: {candidate.authority}\n"
                f"Tipo: {candidate.document_type}\n"
                f"Título oficial: {candidate.official_title}\n"
                f"Descripción: {candidate.description}"
            ),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "regulatory_summary",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "summary": {"type": "string"},
                            "what_published": {"type": "string"},
                            "substance": {"type": "string"},
                        },
                        "required": ["title", "summary", "what_published", "substance"],
                        "additionalProperties": False,
                    },
                },
                "verbosity": "low",
            },
        )
        payload = json.loads(response.output_text)
        return Summary(
            title=concise_title(payload["title"]),
            summary=exactly_30_words(payload["summary"]),
            card_body=build_card_body(
                what_published=payload["what_published"],
                substance=payload["substance"],
                source_url=candidate.url,
            ),
            ai_generated=True,
        )

    @staticmethod
    def _fallback(item: ClassifiedCandidate) -> Summary:
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
            "generado sin modelo de lenguaje; consulta la fuente oficial para el "
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


def _looks_like_effort_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "effort" in message or "reasoning" in message


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
