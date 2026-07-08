from __future__ import annotations

import hashlib
import re
from datetime import date
from urllib.parse import urljoin, urlparse

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

# `#Indice` enlaza cada asunto con un ancla dentro de LA MISMA página
# `gp_hoy.html` ("#Iniciativa5"). Esa página siempre muestra la gaceta del
# día en curso: en cuanto hay una nueva sesión, el enlace publicado ayer
# apunta a la edición de hoy (bug #11: "la fuente se pudre a diario").
#
# Cada edición también se archiva de forma permanente en:
#   https://gaceta.diputados.gob.mx/Gaceta/<legislatura>/<AAAA>/<mes3>/<AAAAMMDD>.html
# (confirmado en vivo el 2026-07-07: /Gaceta/66/2026/jul/20260706.html y
# .../20260707.html responden 200 y conservan las mismas anclas "NombreN"
# que la edición de ese día en gp_hoy.html). Esa URL fechada es estable
# porque el archivo no se regenera; el ancla original sobrevive porque
# apunta al mismo documento archivado, sólo que bajo una ruta permanente en
# vez de la portada "de hoy".
#
# La legislatura no viene expuesta en el índice (sólo en los anexos, que ya
# resuelven su propia fecha/legislatura desde el nombre del PDF); se fija
# aquí como constante. LXVI Legislatura cubre del 1/set/2024 al 31/ago/2027:
# actualizar a "67" a partir de esa fecha.
GACETA_LEGISLATURA = "66"
GACETA_PERMANENT_BASE = "https://gaceta.diputados.gob.mx/Gaceta"
_SPANISH_MONTH_ABBR = {
    1: "ene",
    2: "feb",
    3: "mar",
    4: "abr",
    5: "may",
    6: "jun",
    7: "jul",
    8: "ago",
    9: "sep",
    10: "oct",
    11: "nov",
    12: "dic",
}


def permanent_gaceta_url(published_at: date, fragment: str = "") -> str:
    """URL permanente y fechada de la Gaceta Parlamentaria del día, con el
    ancla del asunto si se conoce (queda sin ancla si no se pudo determinar,
    en vez de arrastrar la ruta rota de `gp_hoy.html`)."""
    month = _SPANISH_MONTH_ABBR[published_at.month]
    base = (
        f"{GACETA_PERMANENT_BASE}/{GACETA_LEGISLATURA}/{published_at.year}/"
        f"{month}/{published_at:%Y%m%d}.html"
    )
    return f"{base}#{fragment}" if fragment else base


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
            # URL permanente fechada en vez de "gp_hoy.html#ancla" (que deja
            # de resolver el asunto en cuanto la portada avanza al día
            # siguiente): se conserva el ancla original si el enlace la trae,
            # y se omite si no (en vez de arrastrar una ruta rota).
            fragment = urlparse(anchor["href"]).fragment
            url = permanent_gaceta_url(published_at, fragment)
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

