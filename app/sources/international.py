from __future__ import annotations

import hashlib
import re
from datetime import date
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from app.models import Candidate
from app.sources.base import (
    Collector,
    SourceContractError,
    require_html_marker,
    require_xml_channel,
)
from app.text import clean_text

# Igual que el sumario del DOF (app/sources/dof.py): se aíslan bloques <item>
# completos con una expresión regular y cada uno se parsea por separado, de
# modo que un bloque dañado o incompleto no tire el resto del feed.
ITEM_BLOCK_RE = re.compile(rb"<item>\s*.*?</item>", re.DOTALL)

# Prefijos de namespace en etiquetas (<dc:creator>, <content:encoded>...). Se
# declaran en la raíz <rss> del feed, no en cada <item>, así que el bloque
# aislado no puede resolverlos y ElementTree falla con "unbound prefix".
NAMESPACE_PREFIX_RE = re.compile(rb"<(/?)[A-Za-z0-9._-]+:")


class RssCollector(Collector):
    """Base compartida para fuentes internacionales que publican RSS 2.0.

    Cada fuente sigue siendo un recolector aislado (falla sin afectar a las
    demás, vía `_collect_source` en app/pipeline.py); esta clase solo evita
    repetir el parser tolerante de bloques <item>.
    """

    url: str
    default_authority: str = ""
    default_document_type: str = "Comunicado"
    # Patrón opcional de títulos a descartar (nodos que no son publicaciones,
    # p. ej. recursos multimedia cuyo título es el nombre del archivo).
    skip_title_re: re.Pattern[str] | None = None

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
        for block in ITEM_BLOCK_RE.findall(payload):
            try:
                item = ElementTree.fromstring(block)
            except ElementTree.ParseError:
                # Feeds WordPress/Drupal traen hijos con prefijo de namespace
                # que rompen el parseo del bloque aislado: se eliminan los
                # prefijos de las etiquetas y se reintenta. Un bloque
                # genuinamente malformado sigue descartándose solo.
                try:
                    item = ElementTree.fromstring(NAMESPACE_PREFIX_RE.sub(rb"<\1", block))
                except ElementTree.ParseError:
                    skipped += 1
                    continue

            title = clean_text(item.findtext("title", ""))
            # Tras quitar prefijos, un <atom:link .../> del ítem se vuelve un
            # <link> sin texto: se toma el primer <link> con contenido real.
            url = clean_text(_first_nonempty_text(item, "link"))
            if not title or not url.lower().startswith(("http://", "https://")):
                continue
            if cls.skip_title_re is not None and cls.skip_title_re.search(title):
                continue

            raw_date = item.findtext("pubDate", "")
            try:
                published_at = parsedate_to_datetime(raw_date).date()
            except (TypeError, ValueError):
                continue
            if published_at < since:
                continue

            guid = clean_text(item.findtext("guid", "")) or url
            description = clean_text(item.findtext("description", "")) or title

            candidates.append(
                Candidate(
                    source=cls.source,
                    source_id=hashlib.sha256(guid.encode()).hexdigest()[:16],
                    url=url,
                    official_title=title,
                    description=description,
                    published_at=published_at,
                    authority=cls.default_authority,
                    document_type=cls.default_document_type,
                )
            )
        return candidates, skipped


def _first_nonempty_text(item: ElementTree.Element, tag: str) -> str:
    for element in item.iter(tag):
        if element.text and element.text.strip():
            return element.text
    return ""


class OnuNoticiasCollector(RssCollector):
    """Noticias ONU (news.un.org), edición en español."""

    source = "ONU Noticias"
    # Certificado en vivo: la página oficial de feeds
    # https://news.un.org/es/rss-feeds lista el feed general en español, y el
    # patrón de ruta (/feed/subscribe/{idioma}/{seccion}/all/...) se verificó
    # con el feed hermano de audio
    # https://news.un.org/feed/subscribe/es/audio-product/all/audio-rss.xml.
    url = "https://news.un.org/feed/subscribe/es/news/all/rss.xml"
    default_authority = "Organización de las Naciones Unidas"
    default_document_type = "Noticia"


class UstrCollector(RssCollector):
    """Comunicados de la Oficina del Representante Comercial de EEUU (USTR)."""

    source = "USTR"
    # Certificado en vivo: el feed general de Drupal expone contenido USTR
    # estructurado y pasa los contratos de transporte, tipo y estructura.
    url = "https://ustr.gov/rss.xml"
    default_authority = "Oficina del Representante Comercial de Estados Unidos (USTR)"
    default_document_type = "Comunicado"


class CpiCollector(RssCollector):
    """Comunicados de la Corte Penal Internacional (CPI / ICC)."""

    source = "CPI"
    # Confirmado en vivo: https://www.icc-cpi.int/rss.xml es RSS 2.0 estándar
    # (pubDate, guid, description con HTML embebido), compatible sin cambios
    # con el parser tolerante de RssCollector.
    #
    # robots.txt (verificado en vivo) declara, para `User-agent: *` (nuestro
    # UA es "RadarRegulatorioMX/0.1 ...", no un bot listado explícitamente):
    #   Content-Signal: search=yes,ai-train=no,use=reference
    #   Allow: /
    # "use=reference" autoriza el uso de referencia (enlazar y citar
    # extractos), que es exactamente lo que hace este recolector: título,
    # enlace y una descripción/resumen, nunca reentrega del sitio completo.
    # El robots sí lista `Disallow: /` para bots específicos (Amazonbot,
    # ClaudeBot, GPTBot, etc.), pero ese bloque no aplica a nuestro
    # User-Agent declarado.
    url = "https://www.icc-cpi.int/rss.xml"
    default_authority = "Corte Penal Internacional"
    default_document_type = "Comunicado"


class CijCollector(Collector):
    """Comunicados de la Corte Internacional de Justicia (CIJ / ICJ)."""

    source = "CIJ"
    # /rss.xml fue retirado y devuelve 404. El índice oficial es HTML estable
    # de Drupal y publica número, fecha, título y PDF oficial por comunicado.
    url = "https://www.icj-cij.org/press-releases"
    default_authority = "Corte Internacional de Justicia"
    default_document_type = "Comunicado"

    async def collect(self, since: date) -> list[Candidate]:
        response = await self.client.get(self.url)
        self.validate_response(response, content_types={"text/html"})
        return self.parse(response.text, since)

    @classmethod
    def parse(cls, payload: str, since: date) -> list[Candidate]:
        require_html_marker(payload, "view-press-releases", source=cls.source)
        soup = BeautifulSoup(payload, "html.parser")
        container = soup.select_one(".view-press-releases .view-content")
        if container is None:
            raise SourceContractError(f"{cls.source}: falta la lista oficial de comunicados")

        candidates: list[Candidate] = []
        for row in container.select(":scope > .views-row"):
            number_anchor = row.select_one(".views-field-field-press-release-number a[href]")
            time_element = row.select_one(".views-field-field-date-of-the-document time")
            title_element = row.select_one(".views-field-field-document-long-title")
            if number_anchor is None or time_element is None or title_element is None:
                continue
            number = clean_text(number_anchor.get_text(" ", strip=True))
            title = clean_text(title_element.get_text(" ", strip=True))
            raw_date = str(time_element.get("datetime") or time_element.get_text(" ", strip=True))
            published_at = _parse_html_date(raw_date)
            if not number or not title or published_at is None or published_at < since:
                continue
            url = urljoin(cls.url, str(number_anchor.get("href")))
            source_id = re.sub(r"^Press release No\.\s*", "", number, flags=re.IGNORECASE)
            candidates.append(
                Candidate(
                    source=cls.source,
                    source_id=source_id,
                    url=url,
                    official_title=title,
                    description=f"{number}. {title}",
                    published_at=published_at,
                    authority=cls.default_authority,
                    document_type=cls.default_document_type,
                )
            )
        return candidates


class TradeGovCollector(Collector):
    """International Trade Administration (Trade.gov, Departamento de Comercio de EEUU)."""

    source = "Trade.gov"
    # El antiguo feed redirige a HTML. Este índice oficial contiene fecha y
    # enlace canónico de los comunicados de la ITA.
    url = "https://www.trade.gov/press-releases"
    default_authority = "International Trade Administration (Trade.gov)"
    default_document_type = "Comunicado"

    async def collect(self, since: date) -> list[Candidate]:
        response = await self.client.get(self.url)
        self.validate_response(response, content_types={"text/html"})
        return self.parse(response.text, since)

    @classmethod
    def parse(cls, payload: str | bytes, since: date) -> list[Candidate]:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="replace")
        require_html_marker(
            payload,
            "Press Releases Issued by the International Trade Administration",
            source=cls.source,
        )
        soup = BeautifulSoup(payload, "html.parser")
        heading = soup.find(
            "h2",
            string=lambda value: bool(value)
            and "Press Releases Issued by the International Trade Administration" in value,
        )
        if heading is None or heading.parent is None:
            raise SourceContractError(f"{cls.source}: falta la lista oficial de comunicados")

        candidates: list[Candidate] = []
        for item in heading.parent.find_all("li"):
            anchor = item.find("a", href=True)
            if anchor is None:
                continue
            url = urljoin(cls.url, str(anchor.get("href")))
            if "/press-release/" not in url and "/feature-article/" not in url:
                continue
            strings = list(item.stripped_strings)
            if not strings:
                continue
            published_at = _parse_html_date(strings[0])
            if published_at is None or published_at < since:
                continue
            title = clean_text(anchor.get_text(" ", strip=True))
            if not title:
                continue
            candidates.append(
                Candidate(
                    source=cls.source,
                    source_id=hashlib.sha256(url.encode()).hexdigest()[:16],
                    url=url,
                    official_title=title,
                    description=title,
                    published_at=published_at,
                    authority=cls.default_authority,
                    document_type=cls.default_document_type,
                )
            )
        return candidates


class OmcCollector(RssCollector):
    """Noticias de la Organización Mundial del Comercio (OMC / WTO)."""

    source = "OMC"
    # Confirmado en vivo desde este entorno (datacenter): RSS 2.0 estándar con
    # pubDate del día. El robots.txt de wto.org veta el rastreo general del
    # sitio (`User-agent: *` / `Disallow: *`), por lo que NO se scrapea nada
    # más; este feed es el canal de suscripción que el propio sitio publica
    # para lectores RSS y solo se citan título/enlace/resumen.
    url = "https://www.wto.org/library/rss/latest_news_e.xml"
    default_authority = "Organización Mundial del Comercio"
    default_document_type = "Noticia"


def _parse_html_date(raw_date: str) -> date | None:
    from datetime import datetime

    normalized_date = clean_text(raw_date).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized_date).date()
    except ValueError:
        pass
    for pattern in ("%m/%d/%y", "%m/%d/%Y", "%d %B %Y"):
        try:
            return datetime.strptime(normalized_date, pattern).date()
        except ValueError:
            continue
    return None
