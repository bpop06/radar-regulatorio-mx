from __future__ import annotations

import hashlib
import re
from datetime import date
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

from app.models import Candidate
from app.sources.base import Collector, require_xml_channel
from app.text import clean_text, parse_date


class DofCollector(Collector):
    source = "DOF"
    # El host ``www`` sirve una cadena TLS distinta e incompleta. El origen
    # canónico sin ``www`` publica el mismo sumario con una cadena verificable.
    url = "https://dof.gob.mx/sumarios/sumario_31dias.xml"

    async def collect(self, since: date) -> list[Candidate]:
        response = await self.client.get(self.url)
        self.validate_response(
            response,
            content_types={"application/rss+xml", "application/xml", "text/xml"},
        )
        candidates, skipped = self.parse_with_diagnostics(response.content, since)
        if skipped:
            self.mark_degraded(f"{skipped} bloques RSS no pudieron interpretarse")
        return candidates

    @classmethod
    def parse(cls, payload: bytes, since: date) -> list[Candidate]:
        return cls.parse_with_diagnostics(payload, since)[0]

    @classmethod
    def parse_with_diagnostics(
        cls, payload: bytes, since: date
    ) -> tuple[list[Candidate], int]:
        require_xml_channel(payload, source=cls.source)
        candidates: list[Candidate] = []
        skipped = 0
        # The official feed occasionally ends with an incomplete CDATA block.
        # Parse complete items independently so one damaged tail does not erase the day.
        item_blocks = re.findall(rb"<item>\s*.*?</item>", payload, flags=re.DOTALL)
        for block in item_blocks:
            try:
                item = ElementTree.fromstring(block)
            except ElementTree.ParseError:
                # A malformed block should not take down the rest of the feed.
                skipped += 1
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
        return candidates, skipped


# El sumario del DOF describe el documento como prosa libre (a veces
# arrancando con el propio órgano emisor, p.ej. "FISCALIA GENERAL DE LA
# REPUBLICA.- Convocatoria..."), no con un tipo documental limpio. Tomar sus
# primeras palabras a secas arrastra basura: números de oficio/acuerdo
# ("Oficio 500-05-00-00-00-2026-16021 Mediante...") y conectores sueltos que
# quedan colgando al recortar a un número fijo de palabras ("Acuerdo Por
# El...", "Instituto Mexicano De..."). Se corta antes de la primera palabra
# con un dígito (el número de acto empieza ahí) y se eliminan los conectores
# finales que puedan quedar colgando tras el recorte.
_DOCUMENT_TYPE_WORDS = 3
_HAS_DIGIT_RE = re.compile(r"\d")
_TRAILING_CONNECTORS = {
    "de",
    "del",
    "por",
    "el",
    "la",
    "los",
    "que",
    "con",
    "mediante",
    "se",
    "cual",
}


def _document_type(title: str) -> str:
    candidate_words = clean_text(title).split()[:_DOCUMENT_TYPE_WORDS]

    words: list[str] = []
    for word in candidate_words:
        if _HAS_DIGIT_RE.search(word):
            break
        words.append(word)

    while words and words[-1].strip(".,;:").lower() in _TRAILING_CONNECTORS:
        words.pop()

    return " ".join(words).title()
