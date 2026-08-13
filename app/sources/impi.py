from __future__ import annotations

import hashlib
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.models import Candidate
from app.sources.base import Collector, SourceContractError
from app.sources.gobmx import GobMxCollector
from app.text import clean_text, parse_date


class ImpiCollector(Collector):
    source = "IMPI"
    # El dominio institucional bloquea intermitentemente con 403. El archivo
    # oficial del propio IMPI en gob.mx publica la misma serie de comunicados
    # con URL, fecha y título estables.
    url = "https://www.gob.mx/impi/archivo/prensa"

    async def collect(self, since: date) -> list[Candidate]:
        candidates: list[Candidate] = []
        seen: set[str] = set()
        for page in range(1, 13):
            response = await self.client.get(
                self.url,
                params={"idiom": "es", "order": "DESC", "page": page},
            )
            self.validate_response(
                response,
                content_types={
                    "application/javascript",
                    "application/json",
                    "text/html",
                    "text/javascript",
                },
            )
            page_items = GobMxCollector.parse_archive(response.text, "impi", "prensa")
            if not page_items:
                if page == 1:
                    # Mantiene compatibilidad con una representación HTML
                    # completa si gob.mx deja de devolver fragmentos JS.
                    html_candidates = self.parse(response.text, since)
                    if not html_candidates:
                        raise SourceContractError(
                            "IMPI: el archivo oficial no contiene artículos reconocibles"
                        )
                    return html_candidates
                break
            for item in page_items:
                if item.published_at < since or item.url in seen:
                    continue
                seen.add(item.url)
                candidates.append(_candidate(item.url, item.title, item.published_at))
            if min(item.published_at for item in page_items) < since:
                break
        return candidates

    @classmethod
    def parse(cls, payload: str, since: date) -> list[Candidate]:
        soup = BeautifulSoup(payload, "html.parser")
        candidates: list[Candidate] = []
        for article in soup.select("article"):
            time_element = article.find("time")
            title_element = article.find(["h3", "h4"])
            anchor = None
            if title_element:
                anchor = title_element.find("a", href=True) or title_element.find_parent(
                    "a", href=True
                )
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
                _candidate(url, title, published_at, description=description)
            )
        return candidates


def _candidate(
    url: str,
    title: str,
    published_at: date,
    *,
    description: str | None = None,
) -> Candidate:
    return Candidate(
        source=ImpiCollector.source,
        source_id=hashlib.sha256(url.encode()).hexdigest()[:16],
        url=url,
        canonical_url=url,
        official_title=title,
        description=description or title,
        published_at=published_at,
        official_published_at=published_at,
        authority="Instituto Mexicano de la Propiedad Industrial",
        document_type="Comunicado",
        official_evidence={"official_notice": url},
    )
