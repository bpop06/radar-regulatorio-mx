from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit

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
from app.sources.base import Collector, SourceContractError
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

# El sitemap incluye campañas, micrositios presidenciales históricos, portales
# de prueba y alias que redirigen a administraciones anteriores. Consultarlos
# todos provocaba cientos de 401/403/500 y convertía una fuente útil en una
# señal de cobertura imposible de interpretar. Este catálogo acota el
# recolector a órganos vigentes y materialmente dentro del radar; incorporar
# otro portal exige fixture y certificación, igual que una fuente nueva.
CERTIFIED_APF_PORTALS = {
    "agricultura",
    "antimonopolio",
    "bienestar",
    "buengobierno",
    "cjef",
    "cnbv",
    "cofepris",
    "conade",
    "conadis",
    "conafor",
    "conagua",
    "consar",
    "defensa",
    "impi",
    "inifed",
    "inm",
    "issste",
    "pensionissste",
    "prodecon",
    "profeco",
    "profepa",
    "salud",
    "sat",
    "se",
    "sectur",
    "sedatu",
    "segob",
    "semar",
    "semarnat",
    "sener",
    "sep",
    "shcp",
    "sict",
    "sre",
    "stps",
    "uif",
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
    "antimonopolio": "Comisión Nacional Antimonopolio",
    "bienestar": "Secretaría de Bienestar",
    "buengobierno": "Secretaría Anticorrupción y Buen Gobierno",
    "cjef": "Consejería Jurídica del Ejecutivo Federal",
    "conade": "Comisión Nacional de Cultura Física y Deporte",
    "conadis": (
        "Consejo Nacional para el Desarrollo y la Inclusión de las Personas con Discapacidad"
    ),
    "conafor": "Comisión Nacional Forestal",
    "conagua": "Comisión Nacional del Agua",
    "cnbv": "Comisión Nacional Bancaria y de Valores",
    "consar": "Comisión Nacional del Sistema de Ahorro para el Retiro",
    "cofepris": "Comisión Federal para la Protección contra Riesgos Sanitarios",
    "defensa": "Secretaría de la Defensa Nacional",
    "epn": "Presidencia de la República",
    "fgr": "Fiscalía General de la República",
    "inifed": "Instituto Nacional de la Infraestructura Física Educativa",
    "inm": "Instituto Nacional de Migración",
    "inpi": "Instituto Nacional de los Pueblos Indígenas",
    "issste": "Instituto de Seguridad y Servicios Sociales de los Trabajadores del Estado",
    "pensionissste": "PENSIONISSSTE",
    "prodecon": "Procuraduría de la Defensa del Contribuyente",
    "profeco": "Procuraduría Federal del Consumidor",
    "profepa": "Procuraduría Federal de Protección al Ambiente",
    "salud": "Secretaría de Salud",
    "sat": "Servicio de Administración Tributaria",
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
    "uif": "Unidad de Inteligencia Financiera",
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


def canonical_gobmx_url(value: str) -> str:
    """Normaliza la URL oficial antes de derivar la identidad de Gob.mx."""

    parts = urlsplit(value.strip())
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"fbclid", "gclid"}
    ]
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), "")
    )


def gobmx_source_id(value: str) -> str:
    """Deriva un identificador estable de la URL canónica, no de variantes de rastreo."""

    canonical_url = canonical_gobmx_url(value)
    return hashlib.sha256(canonical_url.encode()).hexdigest()[:16]


class GobMxCollector(Collector):
    source = "Gob.mx APF"
    index_url = "https://www.gob.mx/sitemap-gobierno.xml"

    def __init__(self, client) -> None:
        super().__init__(client)
        # gob.mx aplica rate limiting por ráfagas incluso con concurrencia
        # moderada. Serializar evita convertir archivos accesibles en falsos
        # 403; ``gather`` conserva el aislamiento entre portales.
        self._request_limit = asyncio.Semaphore(1)

    async def collect(self, since: date) -> list[Candidate]:
        # El catálogo ya está certificado y versionado. No depender del
        # sitemap global evita que campañas históricas/pruebas aparezcan de
        # forma silenciosa y que un 403 transitorio del índice derribe todos
        # los archivos oficiales que siguen disponibles.
        portals = sorted(CERTIFIED_APF_PORTALS)

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
                self.validate_response(
                    response,
                    content_types={
                        "application/javascript",
                        "application/json",
                        "text/html",
                        "text/javascript",
                    },
                )
            except Exception as exc:
                self.mark_degraded(
                    f"{portal}/{kind} página {page}: {type(exc).__name__}: {exc}"
                )
                break

            try:
                page_items, skipped = self.parse_archive_with_diagnostics(
                    response.text, portal, kind
                )
            except SourceContractError as exc:
                self.mark_degraded(f"{portal}/{kind} página {page}: {exc}")
                break
            if skipped:
                self.mark_degraded(
                    f"{portal}/{kind} página {page}: "
                    f"{skipped} registros no pudieron interpretarse"
                )
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
            self.validate_response(response, content_types={"text/html"})
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
        except Exception as exc:
            self.mark_degraded(
                f"detalle {item.url}: {type(exc).__name__}: {exc}; se conserva el extracto"
            )

        canonical_url = canonical_gobmx_url(item.url)
        return Candidate(
            source=self.source,
            source_id=gobmx_source_id(item.url),
            url=item.url,
            canonical_url=canonical_url,
            official_title=item.title,
            description=description,
            published_at=item.published_at,
            authority=authority,
            document_type=document_type,
        )

    @classmethod
    def parse_portals(cls, payload: bytes) -> list[str]:
        if b"<sitemapindex" not in payload.lower():
            raise SourceContractError("Gob.mx APF: respuesta sin sitemapindex")
        portals: list[str] = []
        for raw_location in SITEMAP_LOCATION_RE.findall(payload):
            location = html.unescape(raw_location.decode("utf-8", errors="replace"))
            filename = urlparse(location).path.rsplit("/", 1)[-1]
            match = re.fullmatch(r"sitemap-(.+)\.xml", filename)
            if not match:
                continue
            portal = match.group(1)
            if portal not in NON_INSTITUTIONAL_PORTALS and portal in CERTIFIED_APF_PORTALS:
                portals.append(portal)
        return list(dict.fromkeys(portals))

    @classmethod
    def parse_archive(
        cls, payload: str, portal: str, kind: str
    ) -> list[ArchiveItem]:
        return cls.parse_archive_with_diagnostics(payload, portal, kind)[0]

    @classmethod
    def parse_archive_with_diagnostics(
        cls, payload: str, portal: str, kind: str
    ) -> tuple[list[ArchiveItem], int]:
        html_fragments: list[str] = []
        matches = list(APPEND_RE.finditer(payload))
        if not matches:
            raise SourceContractError(
                "Gob.mx APF: respuesta sin estructura de archivo reconocible"
            )
        for match in matches:
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
        skipped = 0
        for article in soup.select("article"):
            time_element = article.find("time")
            title_element = article.find(["h2", "h3"])
            anchor = article.find("a", href=True)
            if not time_element or not title_element or not anchor:
                skipped += 1
                continue
            raw_date = time_element.get("datetime") or time_element.get("date")
            try:
                published_at = parse_date(str(raw_date or time_element.get_text()))
            except ValueError:
                skipped += 1
                continue
            title = clean_text(title_element.get_text(" ", strip=True))
            url = urljoin("https://www.gob.mx", anchor["href"])
            if not title or not url.lower().startswith(("http://", "https://")):
                skipped += 1
                continue
            items.append(
                ArchiveItem(
                    url=url,
                    title=title,
                    published_at=published_at,
                    portal=portal,
                    document_type="Comunicado" if kind == "prensa" else "Artículo",
                )
            )
        return items, skipped

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
