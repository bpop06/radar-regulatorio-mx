from __future__ import annotations

import hashlib
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.models import Candidate
from app.sources.base import Collector
from app.text import clean_text, parse_date


class ImpiCollector(Collector):
    source = "IMPI"
    url = "https://www.impi.gob.mx/"

    async def collect(self, since: date) -> list[Candidate]:
        response = await self.client.get(self.url)
        response.raise_for_status()
        return self.parse(response.text, since)

    @classmethod
    def parse(cls, payload: str, since: date) -> list[Candidate]:
        soup = BeautifulSoup(payload, "html.parser")
        candidates: list[Candidate] = []
        for article in soup.select("article"):
            time_element = article.find("time")
            title_element = article.find(["h3", "h4"])
            anchor = title_element.find("a", href=True) if title_element else None
            if not time_element or not title_element or not anchor:
                continue
            raw_date = time_element.get("datetime") or time_element.get("date")
            published_at = parse_date(str(raw_date or time_element.get_text()))
            if published_at < since:
                continue
            title = clean_text(title_element.get_text(" ", strip=True))
            description_element = article.find("p")
            description = clean_text(
                description_element.get_text(" ", strip=True) if description_element else title
            )
            url = urljoin(cls.url, anchor["href"])
            candidates.append(
                Candidate(
                    source=cls.source,
                    source_id=hashlib.sha256(url.encode()).hexdigest()[:16],
                    url=url,
                    official_title=title,
                    description=description,
                    published_at=published_at,
                    authority="Instituto Mexicano de la Propiedad Industrial",
                    document_type="Artículo",
                )
            )
        return candidates

