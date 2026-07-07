from __future__ import annotations

import hashlib
import re
from datetime import date
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from app.models import Candidate
from app.sources.base import Collector
from app.text import clean_text

# Igual que el sumario del DOF (app/sources/dof.py): se aíslan bloques <item>
# completos con una expresión regular y cada uno se parsea por separado, de
# modo que un bloque dañado o incompleto no tire el resto del feed.
ITEM_BLOCK_RE = re.compile(rb"<item>\s*.*?</item>", re.DOTALL)


class RssCollector(Collector):
    """Base compartida para fuentes internacionales que publican RSS 2.0.

    Cada fuente sigue siendo un recolector aislado (falla sin afectar a las
    demás, vía `_collect_source` en app/pipeline.py); esta clase solo evita
    repetir el parser tolerante de bloques <item>.
    """

    url: str
    default_authority: str = ""
    default_document_type: str = "Comunicado"

    async def collect(self, since: date) -> list[Candidate]:
        response = await self.client.get(self.url)
        response.raise_for_status()
        return self.parse(response.content, since)

    @classmethod
    def parse(cls, payload: bytes, since: date) -> list[Candidate]:
        candidates: list[Candidate] = []
        for block in ITEM_BLOCK_RE.findall(payload):
            try:
                item = ElementTree.fromstring(block)
            except ElementTree.ParseError:
                # Un bloque malformado no debe tumbar el resto del feed.
                continue

            title = clean_text(item.findtext("title", ""))
            url = clean_text(item.findtext("link", ""))
            if not title or not url.lower().startswith(("http://", "https://")):
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
        return candidates


class OnuNoticiasCollector(RssCollector):
    """Noticias ONU (news.un.org), edición en español."""

    source = "ONU Noticias"
    # Confirmado por búsqueda: la página oficial de feeds
    # https://news.un.org/es/rss-feeds lista el feed general en español, y el
    # patrón de ruta (/feed/subscribe/{idioma}/{seccion}/all/...) se verificó
    # con el feed hermano de audio
    # https://news.un.org/feed/subscribe/es/audio-product/all/audio-rss.xml.
    # No se pudo hacer una petición HTTP en vivo en este entorno (red externa
    # bloqueada); pendiente de verificación en vivo desde la Mac.
    url = "https://news.un.org/feed/subscribe/es/news/all/rss.xml"
    default_authority = "Organización de las Naciones Unidas"
    default_document_type = "Noticia"


class UstrCollector(RssCollector):
    """Comunicados de la Oficina del Representante Comercial de EEUU (USTR)."""

    source = "USTR"
    # USTR mantenía un índice de feeds RSS en
    # https://ustr.gov/archive/Meta_Content/RSS/Section_Index.html (archivado
    # tras el rediseño del sitio); no se localizó un feed RSS activo
    # equivalente para la sección actual de comunicados
    # (https://ustr.gov/about-us/policy-offices/press-office/press-releases).
    # Se usa la ruta convencional de Drupal para el feed general del sitio.
    # PENDIENTE DE VERIFICACIÓN EN VIVO desde la Mac (red externa bloqueada
    # en este entorno): confirmar que expone los comunicados de prensa y, si
    # no, sustituir por el feed correcto una vez detectado con acceso real.
    url = "https://ustr.gov/rss.xml"
    default_authority = "Oficina del Representante Comercial de Estados Unidos (USTR)"
    default_document_type = "Comunicado"


class TradeGovCollector(RssCollector):
    """International Trade Administration (Trade.gov, Departamento de Comercio de EEUU)."""

    source = "Trade.gov"
    # Confirmado por búsqueda: Tradeology, el blog oficial de la International
    # Trade Administration sobre política comercial (incluye relación
    # comercial EEUU-México), publica su feed RSS en blog.trade.gov/feed/.
    # No se localizó un feed RSS dedicado para la sección HTML
    # trade.gov/press-releases; pendiente de verificación en vivo desde la
    # Mac para confirmar vigencia y cobertura.
    url = "https://blog.trade.gov/feed/"
    default_authority = "International Trade Administration (Trade.gov)"
    default_document_type = "Comunicado"
