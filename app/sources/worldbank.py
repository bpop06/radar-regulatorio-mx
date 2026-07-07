from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any

from app.models import Candidate
from app.sources.base import Collector
from app.text import clean_text


class WorldBankCollector(Collector):
    """Comunicados de prensa del Banco Mundial sobre México.

    Usa el buscador público de noticias del Banco Mundial
    (`search.worldbank.org/api/v2/news`) filtrado por país exacto "Mexico" y
    ordenado por fecha de lanzamiento (`lnchdt`) descendente, así que los
    30 registros más recientes alcanzan sobradamente la ventana habitual de
    `lookback_days`. La respuesta mezcla, dentro de `documents`, una entrada
    de metadatos `"facets"` junto a los documentos reales; se descarta
    cualquier entrada sin `title`/`url` reconocibles.
    """

    source = "Banco Mundial"
    url = (
        "https://search.worldbank.org/api/v2/news"
        "?format=json&count_exact=Mexico&rows=30&srt=lnchdt&order=desc"
    )
    authority = "Banco Mundial"
    document_type = "Comunicado"

    async def collect(self, since: date) -> list[Candidate]:
        response = await self.client.get(self.url)
        response.raise_for_status()
        return self.parse(response.json(), since)

    @classmethod
    def parse(cls, payload: dict[str, Any], since: date) -> list[Candidate]:
        candidates: list[Candidate] = []
        documents = payload.get("documents") if isinstance(payload, dict) else None
        if not isinstance(documents, dict):
            return candidates

        for _doc_id, entry in documents.items():
            if not isinstance(entry, dict):
                continue

            title = _cdata_text(entry.get("title"))
            url = clean_text(str(entry.get("url") or ""))
            if not title or not url.lower().startswith(("http://", "https://")):
                continue

            published_at = _parse_wb_date(str(entry.get("lnchdt") or ""))
            if published_at is None or published_at < since:
                continue

            description = _cdata_text(entry.get("descr")) or title
            stable_id = clean_text(str(entry.get("id") or "")) or url

            candidates.append(
                Candidate(
                    source=cls.source,
                    source_id=hashlib.sha256(stable_id.encode()).hexdigest()[:16],
                    url=url,
                    official_title=title,
                    description=description,
                    published_at=published_at,
                    authority=cls.authority,
                    document_type=cls.document_type,
                )
            )
        return candidates


def _cdata_text(value: object) -> str:
    """El API envuelve título/descripción en {"cdata!": "..."} cuando el
    texto original venía en un bloque CDATA; en caso contrario puede llegar
    como string plano. Cualquier otro tipo (None, dict vacío) se trata como
    texto vacío."""
    if isinstance(value, dict):
        return clean_text(str(value.get("cdata!", "")))
    if isinstance(value, str):
        return clean_text(value)
    return ""


def _parse_wb_date(raw_date: str) -> date | None:
    if not raw_date:
        return None
    try:
        return datetime.fromisoformat(raw_date.replace("Z", "+00:00")).date()
    except ValueError:
        return None
