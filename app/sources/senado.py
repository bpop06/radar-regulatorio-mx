from __future__ import annotations

import hashlib
from datetime import date

from app.models import Candidate
from app.sources.base import Collector
from app.text import clean_text, parse_date


class SenadoCollector(Collector):
    source = "Senado"
    url_template = (
        "https://transparenciaparlamentaria.senado.gob.mx/"
        "transparencia_parlamentaria/json/Iniciativas/"
        "iniciativas_proposiciones_{year}.json"
    )

    async def collect(self, since: date) -> list[Candidate]:
        candidates: list[Candidate] = []
        for year in range(since.year, date.today().year + 1):
            response = await self.client.get(self.url_template.format(year=year))
            response.raise_for_status()
            candidates.extend(self.parse(response.json(), since))
        return candidates

    @classmethod
    def parse(cls, payload: dict[str, object], since: date) -> list[Candidate]:
        candidates: list[Candidate] = []
        for entry in payload.get("data", []):  # type: ignore[union-attr]
            document_type = clean_text(str(entry.get("tipo_documento", "")))
            if "iniciativa" not in document_type.lower():
                continue
            published_at = parse_date(str(entry.get("fecha_documento", "")))
            if published_at < since:
                continue
            title = clean_text(str(entry.get("titulo_docuemnto", "")))
            theme = clean_text(str(entry.get("tema_documento", "")))
            url = str(entry.get("link_documento", ""))
            stable = "|".join((url, title, published_at.isoformat()))
            candidates.append(
                Candidate(
                    source=cls.source,
                    source_id=hashlib.sha256(stable.encode()).hexdigest()[:16],
                    url=url,
                    official_title=title,
                    description=theme or title,
                    published_at=published_at,
                    authority=clean_text(str(entry.get("organo", "Senado de la República"))),
                    document_type=document_type,
                )
            )
        return candidates
