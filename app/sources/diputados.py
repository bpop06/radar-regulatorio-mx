from __future__ import annotations

import hashlib
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.models import Candidate
from app.sources.base import Collector
from app.text import clean_text, parse_date


class DiputadosCollector(Collector):
    source = "Diputados"
    url = "https://gaceta.diputados.gob.mx/gp_hoy.html"

    async def collect(self, since: date) -> list[Candidate]:
        response = await self.client.get(self.url)
        response.raise_for_status()
        return self.parse(response.content, since)

    @classmethod
    def parse(cls, payload: bytes, since: date) -> list[Candidate]:
        text = payload.decode("iso-8859-1", errors="replace")
        soup = BeautifulSoup(text, "html.parser")
        page_title = clean_text(soup.title.get_text() if soup.title else "")
        try:
            published_at = parse_date(page_title)
        except ValueError:
            published_at = date.today()
        if published_at < since:
            return []

        candidates: list[Candidate] = []
        index = soup.select_one("#Indice")
        if not index:
            return candidates

        current_section = ""
        for element in index.find_all(["a", "li"]):
            if element.name == "a" and "Seccion" in element.get("class", []):
                current_section = clean_text(element.get_text(" ", strip=True))
                continue
            if element.name != "li":
                continue
            anchor = element.find("a", href=True)
            if not anchor:
                continue
            title = clean_text(anchor.get_text(" ", strip=True))
            if len(title) < 20:
                continue
            url = urljoin(cls.url, anchor["href"])
            candidates.append(
                Candidate(
                    source=cls.source,
                    source_id=hashlib.sha256(url.encode()).hexdigest()[:16],
                    url=url,
                    official_title=title,
                    description=title,
                    published_at=published_at,
                    authority="Cámara de Diputados",
                    document_type=current_section or "Gaceta Parlamentaria",
                )
            )
        return candidates

