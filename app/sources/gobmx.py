from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.models import Candidate
from app.relevance import (
    ADMINISTRATIVE_COURT_TERMS,
    ADMINISTRATIVE_LAW_TERMS,
    APPOINTMENT_ACTION_TERMS,
    APPOINTMENT_EXPLICIT_TERMS,
    CATEGORY_TERMS,
    CONTENTIOUS_EXPLICIT_TERMS,
    CONTENTIOUS_PROCESS_TERMS,
    FEDERAL_POSITION_TERMS,
    FISCAL_CONTENTIOUS_TERMS,
)
from app.sources.base import Collector
from app.text import clean_text, normalized, parse_date

APPEND_RE = re.compile(r"""append\((["'])((?:\\.|(?!\1).)*)\1\)""", re.DOTALL)
SITEMAP_LOCATION_RE = re.compile(rb"<loc>\s*(.*?)\s*</loc>", re.DOTALL)
MAX_ARCHIVE_PAGES = 12
ARCHIVE_KINDS = ("prensa", "articulos")

NON_INSTITUTIONAL_PORTALS = {
    "acciones-sociales",
    "aprendemx",
    "chikungunya-dengue",
    "comisionambiental",
    "comolehago",
    "creditofonacot",
    "crezcamosjuntos",
    "derechoslaborales",
    "economiadigital-desactivado",
    "epn|mexicodigital",
    "evaluaciondocente",
    "historico-instituto",
    "lamarinacercadeti",
    "olimpiadanacional",
    "productividad",
    "proteccionconsular",
    "residuos-solidos",
    "seguridadvial",
    "sintrabajoinfantil",
    "universidadnaval",
}

# Portales cuyo contenido siempre se acepta, sin pasar por el filtro de
# relevancia por título (`looks_relevant`). PRODECON publica sus boletines
# con títulos genéricos ("Boletín 09/2026", "Tarjeta informativa") que nunca
# mencionan explícitamente términos fiscales, aunque su mandato (Procuraduría
# de la Defensa del Contribuyente) es enteramente fiscal: todo lo que
# publica es, por definición, relevante para este radar. Se verificaron en
# vivo también los archivos de prensa de CONDUSEF y PROFECO: a diferencia de
# PRODECON, su contenido real es mayormente protección al consumidor
# genérica y ajena al alcance del radar (alertas de suplantación de
# identidad, precios de alimentos, quejas de aerolíneas, educación
# financiera), así que agregarlos aquí inundaría el radar con ruido; se
# dejan filtrados por título como el resto de los portales.
# Portales institucionales cuyo material es jurídicamente relevante per se
# (títulos genéricos que no pasan el filtro por vocabulario): la defensoría
# fiscal, la supervisora bancaria, la de PLD, la nueva autoridad de
# competencia (CNA, sectorizada a la SE) y PROFECO, pedida expresamente por
# el dueño. El espejo gob.mx de CONDUSEF queda fuera: no publica contenido
# propio (su sitio real está diferido por cadena TLS rota).
ALWAYS_RELEVANT_PORTALS = {"prodecon", "cnbv", "uif", "antimonopolio", "profeco"}

PORTAL_AUTHORITIES = {
    "agricultura": "Secretaría de Agricultura y Desarrollo Rural",
    "bienestar": "Secretaría de Bienestar",
    "buengobierno": "Secretaría Anticorrupción y Buen Gobierno",
    "cjef": "Consejería Jurídica del Ejecutivo Federal",
    "conade": "Comisión Nacional de Cultura Física y Deporte",
    "conadis": (
        "Consejo Nacional para el Desarrollo y la Inclusión de las Personas con Discapacidad"
    ),
    "conafor": "Comisión Nacional Forestal",
    "conagua": "Comisión Nacional del Agua",
    "defensa": "Secretaría de la Defensa Nacional",
    "epn": "Presidencia de la República",
    "fgr": "Fiscalía General de la República",
    "inifed": "Instituto Nacional de la Infraestructura Física Educativa",
    "inm": "Instituto Nacional de Migración",
    "inpi": "Instituto Nacional de los Pueblos Indígenas",
    "issste": "Instituto de Seguridad y Servicios Sociales de los Trabajadores del Estado",
    "pensionissste": "PENSIONISSSTE",
    "prodecon": "Procuraduría de la Defensa del Contribuyente",
    "profepa": "Procuraduría Federal de Protección al Ambiente",
    "salud": "Secretaría de Salud",
    "se": "Secretaría de Economía",
    "sectur": "Secretaría de Turismo",
    "sedatu": "Secretaría de Desarrollo Agrario, Territorial y Urbano",
    "segob": "Secretaría de Gobernación",
    "semar": "Secretaría de Marina",
    "semarnat": "Secretaría de Medio Ambiente y Recursos Naturales",
    "sener": "Secretaría de Energía",
    "sep": "Secretaría de Educación Pública",
    "shcp": "Secretaría de Hacienda y Crédito Público",
    "sict": "Secretaría de Infraestructura, Comunicaciones y Transportes",
    "sre": "Secretaría de Relaciones Exteriores",
    "stps": "Secretaría del Trabajo y Previsión Social",
}

DISCOVERY_TERMS = tuple(
    dict.fromkeys(
        term
        for terms in (
            *CATEGORY_TERMS.values(),
            ADMINISTRATIVE_LAW_TERMS,
            APPOINTMENT_ACTION_TERMS,
            APPOINTMENT_EXPLICIT_TERMS,
            FEDERAL_POSITION_TERMS,
            CONTENTIOUS_EXPLICIT_TERMS,
            CONTENTIOUS_PROCESS_TERMS,
            ADMINISTRATIVE_COURT_TERMS,
            FISCAL_CONTENTIOUS_TERMS,
        )
        for term in terms
    )
) + (
    "presentacion del director",
    "presentacion de la directora",
)


@dataclass(frozen=True)
class ArchiveItem:
    url: str
    title: str
    published_at: date
    portal: str
    document_type: str


class GobMxCollector(Collector):
    source = "Gob.mx APF"
    index_url = "https://www.gob.mx/sitemap-gobierno.xml"

    def __init__(self, client) -> None:
        super().__init__(client)
        self._request_limit = asyncio.Semaphore(12)

    async def collect(self, since: date) -> list[Candidate]:
        response = await self.client.get(self.index_url)
        response.raise_for_status()
        portals = self.parse_portals(response.content)

        archive_results = await asyncio.gather(
            *(
                self._collect_archive(portal, kind, since)
                for portal in portals
                for kind in ARCHIVE_KINDS
            )
        )
        items = {
            item.url: item
            for result in archive_results
            for item in result
            if item.portal in ALWAYS_RELEVANT_PORTALS or self.looks_relevant(item.title)
        }
        candidates = await asyncio.gather(
            *(self._enrich(item) for item in items.values())
        )
        return [candidate for candidate in candidates if candidate is not None]

    async def _collect_archive(
        self, portal: str, kind: str, since: date
    ) -> list[ArchiveItem]:
        collected: list[ArchiveItem] = []
        for page in range(1, MAX_ARCHIVE_PAGES + 1):
            url = f"https://www.gob.mx/{portal}/archivo/{kind}"
            try:
                async with self._request_limit:
                    response = await self.client.get(
                        url,
                        params={"idiom": "es", "order": "DESC", "page": page},
                    )
                if response.status_code == 404:
                    break
                response.raise_for_status()
            except Exception:
                break

            page_items = self.parse_archive(response.text, portal, kind)
            if not page_items:
                break
            collected.extend(item for item in page_items if item.published_at >= since)
            if min(item.published_at for item in page_items) < since:
                break
        return collected

    async def _enrich(self, item: ArchiveItem) -> Candidate | None:
        description = item.title
        authority = PORTAL_AUTHORITIES.get(
            item.portal, f"Portal federal {item.portal.upper()}"
        )
        document_type = item.document_type
        try:
            async with self._request_limit:
                response = await self.client.get(item.url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            body = soup.select_one(".article-body")
            if body:
                description = clean_text(body.get_text(" ", strip=True))
            metadata = soup.select_one("section.small p")
            if metadata:
                parts = [
                    clean_text(part)
                    for part in metadata.get_text(" ", strip=True).split("|")
                ]
                if parts and parts[0]:
                    authority = parts[0]
                if len(parts) >= 3 and parts[-1]:
                    document_type = parts[-1]
        except Exception:
            pass

        return Candidate(
            source=self.source,
            source_id=hashlib.sha256(item.url.encode()).hexdigest()[:16],
            url=item.url,
            official_title=item.title,
            description=description,
            published_at=item.published_at,
            authority=authority,
            document_type=document_type,
        )

    @classmethod
    def parse_portals(cls, payload: bytes) -> list[str]:
        portals: list[str] = []
        for raw_location in SITEMAP_LOCATION_RE.findall(payload):
            location = html.unescape(raw_location.decode("utf-8", errors="replace"))
            filename = urlparse(location).path.rsplit("/", 1)[-1]
            match = re.fullmatch(r"sitemap-(.+)\.xml", filename)
            if not match:
                continue
            portal = match.group(1)
            if portal not in NON_INSTITUTIONAL_PORTALS:
                portals.append(portal)
        return list(dict.fromkeys(portals))

    @classmethod
    def parse_archive(
        cls, payload: str, portal: str, kind: str
    ) -> list[ArchiveItem]:
        html_fragments: list[str] = []
        for match in APPEND_RE.finditer(payload):
            quote, encoded = match.group(1), match.group(2)
            if quote == '"':
                try:
                    html_fragments.append(json.loads(f'"{encoded}"'))
                    continue
                except json.JSONDecodeError:
                    pass
            html_fragments.append(cls._decode_js_string(encoded))

        soup = BeautifulSoup("".join(html_fragments), "html.parser")
        items: list[ArchiveItem] = []
        for article in soup.select("article"):
            time_element = article.find("time")
            title_element = article.find(["h2", "h3"])
            anchor = article.find("a", href=True)
            if not time_element or not title_element or not anchor:
                continue
            raw_date = time_element.get("datetime") or time_element.get("date")
            try:
                published_at = parse_date(str(raw_date or time_element.get_text()))
            except ValueError:
                continue
            title = clean_text(title_element.get_text(" ", strip=True))
            url = urljoin("https://www.gob.mx", anchor["href"])
            items.append(
                ArchiveItem(
                    url=url,
                    title=title,
                    published_at=published_at,
                    portal=portal,
                    document_type="Comunicado" if kind == "prensa" else "Artículo",
                )
            )
        return items

    @staticmethod
    def looks_relevant(title: str) -> bool:
        text = normalized(title)
        return any(GobMxCollector._contains_title_term(text, term) for term in DISCOVERY_TERMS)

    @staticmethod
    def _contains_title_term(text: str, term: str) -> bool:
        normalized_term = normalized(term)
        if normalized_term.replace(" ", "").isalnum():
            return (
                re.search(rf"(?<!\w){re.escape(normalized_term)}(?!\w)", text)
                is not None
            )
        return normalized_term in text

    @staticmethod
    def _decode_js_string(value: str) -> str:
        return (
            value.replace(r"\/", "/")
            .replace(r"\n", "\n")
            .replace(r"\r", "\r")
            .replace(r"\t", "\t")
            .replace(r"\"", '"')
            .replace(r"\'", "'")
            .replace(r"\\", "\\")
        )
