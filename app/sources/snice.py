from __future__ import annotations

import hashlib
import re
from datetime import date

from bs4 import BeautifulSoup

from app.models import Candidate
from app.sources.base import Collector, require_html_marker
from app.text import clean_text, parse_date
from app.url_policy import OfficialUrlError, resolve_official_link

_DATE_PREFIX_RE = re.compile(r"^\d{2}[./]\d{2}[./]\d{4}\s*")
_EMPTY_PROJECTS_STATUSES = {
    "sin proyectos",
    "sin proyectos en materia de comercio exterior",
}


def _is_empty_projects_status(title: str) -> bool:
    status = _DATE_PREFIX_RE.sub("", title).strip(" .").casefold()
    return status in _EMPTY_PROJECTS_STATUSES


class SniceCollector(Collector):
    source = "SNICE"
    url = "https://www.snice.gob.mx/cs/avi/snice/home.html"

    async def collect(self, since: date) -> list[Candidate]:
        response = await self.client.get(self.url)
        self.validate_response(response, content_types={"text/html"})
        return self.parse(response.text, since)

    @classmethod
    def parse(cls, payload: str, since: date) -> list[Candidate]:
        require_html_marker(payload, "ACTUALIDAD", source=cls.source)
        soup = BeautifulSoup(payload, "html.parser")
        candidates: list[Candidate] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
            title = clean_text(anchor.get_text(" ", strip=True))
            if (
                len(title) < 25
                or "histórico" in title.lower()
                or _is_empty_projects_status(title)
            ):
                continue
            context = clean_text(anchor.parent.get_text(" ", strip=True))
            date_match = re.search(r"\b\d{2}[./]\d{2}[./]\d{4}\b", context)
            if not date_match:
                continue
            published_at = parse_date(date_match.group())
            if published_at < since:
                continue
            try:
                url = resolve_official_link(cls.url, str(anchor["href"]))
            except OfficialUrlError:
                # Un enlace absoluto ajeno no adquiere procedencia oficial
                # sólo porque apareció en la portada de SNICE.
                continue
            if url in seen:
                continue
            seen.add(url)
            candidates.append(
                Candidate(
                    source=cls.source,
                    source_id=hashlib.sha256(url.encode()).hexdigest()[:16],
                    url=url,
                    official_title=_DATE_PREFIX_RE.sub("", title),
                    description=context,
                    published_at=published_at,
                    authority="Secretaría de Economía",
                    document_type="Actualidad",
                )
            )
        return candidates
