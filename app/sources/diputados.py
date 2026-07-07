from __future__ import annotations

import hashlib
import re
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from app.models import Candidate
from app.sources.base import Collector
from app.text import clean_text, parse_date

# Nombre de archivo de los anexos del Pleno: /PDF/<legislatura>/AAAA/mes/AAAAMMDD-<n>.pdf
# (ej. /PDF/66/2026/jul/20260706-I.pdf). No se ancla la legislatura ("66") ni
# la carpeta de mes porque ambas cambian entre legislaturas/meses; la fecha
# se toma del prefijo AAAAMMDD del nombre de archivo, no del título de la
# gaceta, porque un anexo puede documentar una sesión de días previos (p.ej.
# de la Comisión Permanente).
ANEXO_FILENAME_RE = re.compile(r"(\d{4})(\d{2})(\d{2})-[^./]+\.pdf$", re.IGNORECASE)
ANEXO_LABEL_RE = re.compile(r"anexo\s+(\S+)", re.IGNORECASE)


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
        if index:
            candidates.extend(cls._parse_index(index, published_at))

        anexos = soup.select_one("#Anexos")
        if anexos:
            candidates.extend(cls._parse_anexos(anexos, since))

        return candidates

    @classmethod
    def _parse_index(cls, index: Tag, published_at: date) -> list[Candidate]:
        candidates: list[Candidate] = []
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

    @classmethod
    def _parse_anexos(cls, anexos: Tag, since: date) -> list[Candidate]:
        """Anexos del Pleno (dictámenes y minutas en PDF) listados en
        `<div id="Anexos">`: cada uno es un enlace "Anexo <N>" seguido de una
        descripción breve de su contenido, dentro del mismo `<p>`."""
        candidates: list[Candidate] = []
        for anchor in anexos.find_all("a", href=True):
            href = anchor["href"]
            filename_match = ANEXO_FILENAME_RE.search(href)
            if not filename_match:
                continue
            year, month, day = filename_match.groups()
            try:
                published_at = date(int(year), int(month), int(day))
            except ValueError:
                continue
            if published_at < since:
                continue

            label = clean_text(anchor.get_text(" ", strip=True))
            label_match = ANEXO_LABEL_RE.search(label)
            number = label_match.group(1) if label_match else label

            container = anchor.find_parent("p") or anchor.parent
            full_text = clean_text(container.get_text(" ", strip=True)) if container else label
            description = full_text
            if label and full_text.startswith(label):
                description = full_text[len(label) :].strip(" :-")
            description = description or label or f"Anexo {number}"

            url = urljoin(cls.url, href)
            candidates.append(
                Candidate(
                    source=cls.source,
                    source_id=hashlib.sha256(url.encode()).hexdigest()[:16],
                    url=url,
                    official_title=(
                        f"Anexo {number} de la Gaceta Parlamentaria "
                        "(dictámenes y minutas del Pleno)"
                    ),
                    description=description,
                    published_at=published_at,
                    authority="Cámara de Diputados",
                    document_type="Dictámenes y minutas",
                )
            )
        return candidates

