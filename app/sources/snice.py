from __future__ import annotations

import hashlib
import re
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.models import Candidate
from app.sources.base import Collector
from app.text import clean_text, parse_date


class SniceCollector(Collector):
    source = "SNICE"
    url = "https://www.snice.gob.mx/cs/avi/snice/home.html"

    async def collect(self, since: date) -> list[Candidate]:
        response = await self.client.get(self.url)
        response.raise_for_status()
        return self.parse(response.text, since)

    @classmethod
    def parse(cls, payload: str, since: date) -> list[Candidate]:
        soup = BeautifulSoup(payload, "html.parser")
        candidates: list[Candidate] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
            title = clean_text(anchor.get_text(" ", strip=True))
            if len(title) < 25 or "histórico" in title.lower():
                continue
            context = clean_text(anchor.parent.get_text(" ", strip=True))
            date_match = re.search(r"\b\d{2}[./]\d{2}[./]\d{4}\b", context)
            if not date_match:
                continue
            published_at = parse_date(date_match.group())
            if published_at < since:
                continue
            url = urljoin(cls.url, anchor["href"])
            if url in seen:
                continue
            seen.add(url)
            candidates.append(
                Candidate(
                    source=cls.source,
                    source_id=hashlib.sha256(url.encode()).hexdigest()[:16],
                    url=url,
                    official_title=re.sub(r"^\d{2}[./]\d{2}[./]\d{4}\s*", "", title),
                    description=context,
                    published_at=published_at,
                    authority="Secretaría de Economía",
                    document_type="Actualidad",
                )
            )
        return candidates

