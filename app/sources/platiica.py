from __future__ import annotations

from datetime import UTC, date, datetime, time
from urllib.parse import urlparse

from app.models import Candidate
from app.sources.base import Collector
from app.text import clean_text


class PlatiicaCollector(Collector):
    source = "PLATIICA"
    api_url = "https://platiica.economia.gob.mx/wp-json/wp/v2/posts"
    public_origin = "https://platiica.economia.gob.mx"

    async def collect(self, since: date) -> list[Candidate]:
        after = datetime.combine(since, time.min, tzinfo=UTC).isoformat()
        response = await self.client.get(
            self.api_url,
            params={
                "per_page": 100,
                "modified_after": after,
                "orderby": "modified",
                "order": "desc",
                "_fields": "id,date,modified,link,slug,title,excerpt",
            },
        )
        response.raise_for_status()
        return self.parse(response.json(), since)

    @classmethod
    def parse(cls, payload: list[dict[str, object]], since: date) -> list[Candidate]:
        candidates: list[Candidate] = []
        for post in payload:
            modified = datetime.fromisoformat(str(post["modified"])).date()
            if modified < since:
                continue
            slug = str(post.get("slug") or "").strip("/")
            raw_link = str(post.get("link") or "")
            path = urlparse(raw_link).path.strip("/") or slug
            url = f"{cls.public_origin}/{path}/"
            title = clean_text(str(post["title"]["rendered"]))  # type: ignore[index]
            excerpt = clean_text(str(post["excerpt"]["rendered"]))  # type: ignore[index]
            candidates.append(
                Candidate(
                    source=cls.source,
                    source_id=str(post["id"]),
                    url=url,
                    official_title=title,
                    description=excerpt,
                    published_at=modified,
                    authority="Secretaría de Economía",
                    document_type="Normalización",
                )
            )
        return candidates
