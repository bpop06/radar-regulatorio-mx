from __future__ import annotations

import hashlib
import re
from datetime import date
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

from app.models import Candidate
from app.sources.base import Collector
from app.text import clean_text, parse_date


class DofCollector(Collector):
    source = "DOF"
    url = "https://www.dof.gob.mx/sumarios/sumario_31dias.xml"

    async def collect(self, since: date) -> list[Candidate]:
        response = await self.client.get(self.url)
        response.raise_for_status()
        return self.parse(response.content, since)

    @classmethod
    def parse(cls, payload: bytes, since: date) -> list[Candidate]:
        candidates: list[Candidate] = []
        # The official feed occasionally ends with an incomplete CDATA block.
        # Parse complete items independently so one damaged tail does not erase the day.
        item_blocks = re.findall(rb"<item>\s*.*?</item>", payload, flags=re.DOTALL)
        for block in item_blocks:
            try:
                item = ElementTree.fromstring(block)
            except ElementTree.ParseError:
                # A malformed block should not take down the rest of the feed.
                continue
            url = clean_text(item.findtext("link", ""))
            raw_date = item.findtext("valueDate", "")
            if not raw_date:
                raw_date = parse_qs(urlparse(url).query).get("fecha", [""])[0]
            try:
                published_at = parse_date(raw_date)
            except ValueError:
                continue
            if published_at < since:
                continue
            query = parse_qs(urlparse(url).query)
            source_id = query.get("codigo", [""])[0]
            if not source_id:
                source_id = hashlib.sha256(url.encode()).hexdigest()[:16]
            candidates.append(
                Candidate(
                    source=cls.source,
                    source_id=source_id,
                    url=url,
                    official_title=clean_text(item.findtext("description", "")),
                    description=clean_text(item.findtext("description", "")),
                    published_at=published_at,
                    authority=clean_text(item.findtext("title", "")),
                    document_type=_document_type(item.findtext("description", "")),
                )
            )
        return candidates


def _document_type(title: str) -> str:
    first_words = clean_text(title).split()[:3]
    return " ".join(first_words).title()
