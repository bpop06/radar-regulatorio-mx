from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.models import ClassifiedCandidate
from app.text import concise_title, exactly_30_words, normalized, words


@dataclass(frozen=True)
class Summary:
    title: str
    summary: str
    ai_generated: bool


class Summarizer:
    def __init__(self, api_key: str | None, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self._client = None

    def summarize(self, item: ClassifiedCandidate) -> Summary:
        if self.api_key:
            try:
                return self._summarize_with_openai(item)
            except Exception:
                pass
        return self._fallback(item)

    def _summarize_with_openai(self, item: ClassifiedCandidate) -> Summary:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key)

        candidate = item.candidate
        response = self._client.responses.create(
            model=self.model,
            store=False,
            reasoning={"effort": "low"},
            instructions=(
                "Eres analista jurídico mexicano. Identifica la idea de mayor impacto. "
                "No inventes efectos, fechas ni obligaciones. El título debe ser ejecutivo, "
                "neutral y tener máximo 12 palabras. El resumen debe ser claro y autosuficiente."
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
                        },
                        "required": ["title", "summary"],
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
        return Summary(
            title=concise_title(title_base),
            summary=exactly_30_words(summary_base),
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
