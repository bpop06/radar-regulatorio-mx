from __future__ import annotations

import hashlib
import re
from datetime import date
from html import unescape
from urllib.parse import urljoin

from app.models import Candidate
from app.sources.base import Collector
from app.text import SPANISH_MONTHS, clean_text


class TfjaCollector(Collector):
    """Acuerdos de la Sala Superior del TFJA (suspensiones de plazos,
    guardias, sesiones), desde su página anual de acuerdos.

    Estructura verificada en vivo (2026): bloques
    `<p><strong>Lunes 15 de junio de 2026</strong></p>` seguidos de
    `<ul><li><a href=".../SS-12-2026.pdf">SS/12/2026</a><br/> DESCRIPCIÓN</li>`.
    robots.txt del sitio: `User-agent: *` con `Disallow:` vacío (permitido) y
    sitemap declarado.
    """

    source = "TFJA"
    base_url = "https://www.tfja.gob.mx"
    url_template = "https://www.tfja.gob.mx/acuerdos/acuerdos_{year}/"
    authority = "Tribunal Federal de Justicia Administrativa"
    document_type = "Acuerdo"

    DATE_RE = re.compile(
        r"<strong>\s*[A-Za-zÁÉÍÓÚáéíóúé]+\s+(\d{1,2})\s+de\s+([a-zá-ú]+)\s+de\s+(\d{4})\s*</strong>",
        re.IGNORECASE,
    )
    ITEM_RE = re.compile(
        r'<li>\s*<a\s+href="(?P<href>[^"]+\.pdf)"[^>]*>(?P<num>[^<]+)</a>\s*(?:<br\s*/?>)?\s*(?P<desc>.*?)</li>',
        re.IGNORECASE | re.DOTALL,
    )

    async def collect(self, since: date) -> list[Candidate]:
        candidates: list[Candidate] = []
        for year in sorted({since.year, date.today().year}):
            response = await self.client.get(self.url_template.format(year=year))
            response.raise_for_status()
            candidates.extend(self.parse(response.text, since))
        return candidates

    @classmethod
    def parse(cls, payload: str, since: date) -> list[Candidate]:
        candidates: list[Candidate] = []
        # Se recorre el documento en orden: cada encabezado de fecha aplica a
        # los <li> que le siguen hasta el próximo encabezado.
        events = sorted(
            [(m.start(), "date", m) for m in cls.DATE_RE.finditer(payload)]
            + [(m.start(), "item", m) for m in cls.ITEM_RE.finditer(payload)]
        )
        current_date: date | None = None
        for _pos, kind, match in events:
            if kind == "date":
                day, month_name, year = match.groups()
                month = SPANISH_MONTHS.get(month_name.lower())
                if month:
                    try:
                        current_date = date(int(year), month, int(day))
                    except ValueError:
                        current_date = None
                continue

            if current_date is None or current_date < since:
                continue
            number = clean_text(unescape(match.group("num")))
            description = clean_text(re.sub(r"<[^>]+>", " ", unescape(match.group("desc"))))
            if not description:
                description = number
            url = urljoin(cls.base_url, match.group("href"))
            candidates.append(
                Candidate(
                    source=cls.source,
                    source_id=hashlib.sha256(url.encode()).hexdigest()[:16],
                    url=url,
                    official_title=f"Acuerdo {number} del TFJA. {description}",
                    description=description,
                    published_at=current_date,
                    authority=cls.authority,
                    document_type=cls.document_type,
                )
            )
        return candidates
