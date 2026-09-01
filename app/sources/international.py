from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
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
from app.url_policy import OfficialUrlError, resolve_official_link

# Igual que el sumario del DOF (app/sources/dof.py): se aíslan bloques <item>
# completos con una expresión regular y cada uno se parsea por separado, de
# modo que un bloque dañado o incompleto no tire el resto del feed.
ITEM_BLOCK_RE = re.compile(rb"<item>\s*.*?</item>", re.DOTALL)

# Prefijos de namespace en etiquetas (<dc:creator>, <content:encoded>...). Se
# declaran en la raíz <rss> del feed, no en cada <item>, así que el bloque
# aislado no puede resolverlos y ElementTree falla con "unbound prefix".
NAMESPACE_PREFIX_RE = re.compile(rb"<(/?)[A-Za-z0-9._-]+:")
ICJ_CASE_PATH_RE = re.compile(
    r"^/sites/default/files/case-related/(?P<number>[1-9]\d{0,3})/[^/]+\.pdf$",
    re.IGNORECASE,
)
ICJ_STORED_CASE_RE = re.compile(
    r"\bCase\s+(?P<number>[1-9]\d{0,3})\s*-\s*"
    r"(?P<claim>[^()]{3,300}?)\s*"
    r"\((?P<parties>[^()]{2,200}\bv\.?\s+[^()]{2,200})\)"
    r"(?=\s+(?:Number\s*\(|Date\s+of\s+the\s+Document|Document\s+File|"
    r"Document\s+Long\s+Title|Order)\b|$)",
    re.IGNORECASE,
)

ICC_CASE_NUMBER_RE = re.compile(r"\bICC-\d{2}/\d{2}(?:-\d{2}/\d{2})?\b", re.IGNORECASE)
ICC_PARTIES_RE = re.compile(
    r"\b(?P<parties>(?:The\s+)?Prosecutor\s+v\.?\s+[^.;:()]{2,160}?)"
    r"(?=\s+(?:\(|concerns?\b|involves?\b|relates?\s+to\b|was\b|is\b|has\b)|[.;:]|$)",
    re.IGNORECASE,
)
ICC_CLAIM_RE = re.compile(
    # Se permiten puntos dentro de la ventana porque la carátula usa ``v.``
    # y el número de expediente puede contener puntuación; el punto final de
    # la litis sigue delimitado por el grupo ``claim``.
    r"\b(?:case|proceedings)\b[^;]{0,180}?"
    r"\b(?:concerns?|involves?|relates?\s+to)\s+(?P<claim>[^.;]{3,300})",
    re.IGNORECASE,
)
ICC_CHARGES_RE = re.compile(
    r"\bcharges?\s+(?:of|for)\s+(?P<claim>[^.;]{3,300})",
    re.IGNORECASE,
)
ICC_CONVICTION_CLAIM_RE = re.compile(
    r"\bconvicted\s+for\s+"
    r"(?P<claim>(?:the\s+)?(?:war\s+crime|crimes?\s+against\s+humanity|genocide)"
    r"[^.;]{3,300})",
    re.IGNORECASE,
)
ICC_OUTCOME_RE = re.compile(
    r"\b(?:convicts?|acquits?|sentences?|rejects?|dismisses?|confirms?|orders?|"
    r"terminates?|closes?|issues?\s+(?:an?\s+)?(?:warrants?\s+of\s+arrest|"
    r"reparations?\s+orders?))\b|\binstruct(?:s|ed)?\b[^.;]{0,160}\bto\s+pay\b",
    re.IGNORECASE,
)
EXPLICIT_STATUS_RE = re.compile(
    r"\b(?:is|are|remains?)\s+(?:pending|ongoing|final|closed|adjourned|outstanding)\b",
    re.IGNORECASE,
)
ICC_REPARATIONS_COMPLETION_RE = re.compile(
    r"\b(?:has\s+)?(?:fully\s+)?(?:completed\s+the\s+implementation\s+of|implemented)"
    r"\s+the\s+reparations?\s+programme\b",
    re.IGNORECASE,
)
CIJ_OUTCOME_RE = re.compile(
    r"\b(?:rejects?|dismisses?|upholds?|finds?|orders?|declares?|decides?|rules?|"
    r"convicts?|acquits?|sentences?)\b|(?<!\bto\s)\bdelivers?\s+"
    r"(?:its\s+)?(?:judgment|advisory opinion|order)\b|\bjudgment\s+delivered\b",
    re.IGNORECASE,
)
CIJ_TERMINAL_STATUS_RE = re.compile(
    r"\b(?:removes?\s+the\s+case\s+from\s+(?:the\s+Court['’]s|its)\s+List|"
    r"proceedings\s+(?:are\s+)?(?:terminated|discontinued)|"
    r"case\s+(?:is\s+)?(?:closed|removed))\b",
    re.IGNORECASE,
)
AMOUNT_RE = re.compile(
    r"(?:\b(?:US\$|USD|EUR)\s*|€\s*|\$\s*)\d[\d.,]*"
    r"(?:\s*(?:million|billion|millones?|mil\s+millones))?\b"
    r"|\b\d[\d.,]*\s*(?:million|billion|millones?|mil\s+millones)?\s*"
    r"(?:euros?|dollars?|d[oó]lares?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExtractedCase:
    """Literal case metadata found in an official title or description."""

    number: str = ""
    parties: str = ""
    claim: str = ""
    status: str = ""
    outcome: str = ""
    reasoning: str = ""
    amount: str = ""
    evidence: dict[str, str] = field(default_factory=dict)


class RssCollector(Collector):
    """Base compartida para fuentes internacionales que publican RSS 2.0.

    Cada fuente sigue siendo un recolector aislado (falla sin afectar a las
    demás, vía `_collect_source` en app/pipeline.py); esta clase solo evita
    repetir el parser tolerante de bloques <item>.
    """

    url: str
    default_authority: str = ""
    default_document_type: str = "Comunicado"
    allowed_hosts: tuple[str, ...] = ()
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
            raw_url = clean_text(_first_nonempty_text(item, "link"))
            try:
                url = resolve_official_link(
                    cls.url,
                    raw_url,
                    allowed_hosts=cls.allowed_hosts or None,
                )
            except OfficialUrlError:
                skipped += 1
                continue
            if not title:
                skipped += 1
                continue
            if cls.skip_title_re is not None and cls.skip_title_re.search(title):
                continue

            raw_date = item.findtext("pubDate", "")
            try:
                published_at = parsedate_to_datetime(raw_date).date()
            except (TypeError, ValueError):
                skipped += 1
                continue
            if published_at < since:
                continue

            guid = clean_text(item.findtext("guid", "")) or url
            description = clean_text(item.findtext("description", "")) or title
            case = extract_international_case(cls.source, title, description, url)

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
                    case_number=case.number,
                    case_parties=case.parties,
                    case_status=case.status,
                    case_claim=case.claim,
                    case_outcome=case.outcome,
                    case_reasoning=case.reasoning,
                    case_amount=case.amount,
                    official_evidence=case.evidence,
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
    # El feed oficial también publica entradas antiguas de Tradeology en el
    # subdominio institucional blog.trade.gov; no se permiten otros destinos.
    allowed_hosts = ("ustr.gov", "blog.trade.gov")
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
            try:
                url = resolve_official_link(cls.url, str(number_anchor.get("href")))
            except OfficialUrlError:
                continue
            source_id = re.sub(r"^Press release No\.\s*", "", number, flags=re.IGNORECASE)
            description = f"{number}. {title}"
            case = extract_international_case(cls.source, title, description, url)
            candidates.append(
                Candidate(
                    source=cls.source,
                    source_id=source_id,
                    url=url,
                    official_title=title,
                    description=description,
                    published_at=published_at,
                    authority=cls.default_authority,
                    document_type=cls.default_document_type,
                    case_number=case.number,
                    case_parties=case.parties,
                    case_status=case.status,
                    case_claim=case.claim,
                    case_outcome=case.outcome,
                    case_reasoning=case.reasoning,
                    case_amount=case.amount,
                    official_evidence=case.evidence,
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
            try:
                url = resolve_official_link(cls.url, str(anchor.get("href")))
            except OfficialUrlError:
                continue
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


def extract_international_case(
    source: str,
    title: str,
    description: str,
    official_url: str = "",
) -> ExtractedCase:
    """Extrae sólo metadatos literales contenidos en evidencia oficial almacenada."""

    if source == "CIJ":
        return _extract_cij_case(title, description, official_url)
    if source == "CPI":
        return _extract_cpi_case(title, description)
    return ExtractedCase()


def _extract_cij_case(
    title: str,
    description: str,
    official_url: str = "",
) -> ExtractedCase:
    # Las carátulas oficiales de la CIJ terminan en ``(Estado A v. Estado B)``.
    # No se interpreta el objeto de la controversia: se conserva literalmente
    # el texto anterior a la carátula y la actuación posterior al guión.
    case_number = _icj_case_number_from_official_url(official_url)
    caption = re.search(
        r"\((?P<parties>[^()]{2,200}\bv\.?\s+[^()]{2,200})\)",
        title,
        flags=re.IGNORECASE,
    )
    stored_case = None
    if caption is None and _is_official_icj_url(official_url):
        stored_case = ICJ_STORED_CASE_RE.search(description)
        stored_number = clean_text(stored_case.group("number")) if stored_case else ""
        # Una URL de expediente y un bloque de descripción contradictorios no
        # se fusionan. Se conserva únicamente el número aportado por la ruta
        # oficial, que es la evidencia más acotada.
        if case_number and stored_number and stored_number != case_number:
            stored_case = None
        elif not case_number:
            case_number = stored_number

    if caption is None and stored_case is None:
        return ExtractedCase(
            number=case_number,
            evidence={"case_number": case_number} if case_number else {},
        )

    if caption is not None:
        parties = clean_text(caption.group("parties"))
        claim = clean_text(title[: caption.start()].strip(" -"))
        procedure = clean_text(title[caption.end() :].strip(" -"))
        literal_caption = clean_text(caption.group(0))
    else:
        assert stored_case is not None
        parties = clean_text(stored_case.group("parties"))
        claim = clean_text(stored_case.group("claim").strip(" -"))
        # En los nodos históricos el título oficial es la actuación y el
        # bloque estructurado ``Case N - ...`` aporta carátula y litis.
        procedure = clean_text(title)
        literal_caption = f"({parties})"
    amount = _explicit_amount(f"{title}. {description}")
    outcome = procedure if procedure and CIJ_OUTCOME_RE.search(procedure) else ""
    if outcome:
        terminal = CIJ_TERMINAL_STATUS_RE.search(procedure)
        status = clean_text(terminal.group(0)) if terminal else ""
    else:
        status = procedure

    evidence = {
        "case_caption": literal_caption,
        "litis": claim,
    }
    if case_number:
        evidence["case_number"] = case_number
    if procedure:
        evidence["procedural_update"] = procedure
    if outcome:
        evidence["outcome"] = outcome
    if status:
        evidence["status"] = status
    if amount:
        evidence["amount"] = amount
    return ExtractedCase(
        number=case_number,
        parties=parties,
        claim=claim,
        status=status,
        outcome=outcome,
        amount=amount,
        evidence=evidence,
    )


def _icj_case_number_from_official_url(value: str) -> str:
    """Return the docket path segment only for an official CIJ case PDF URL."""

    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    if parts.scheme != "https" or (parts.hostname or "").casefold() not in {
        "icj-cij.org",
        "www.icj-cij.org",
    }:
        return ""
    match = ICJ_CASE_PATH_RE.fullmatch(parts.path)
    if match is None:
        return ""
    return match.group("number")


def _is_official_icj_url(value: str) -> bool:
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme == "https" and (parts.hostname or "").casefold() in {
        "icj-cij.org",
        "www.icj-cij.org",
    }


def _extract_cpi_case(title: str, description: str) -> ExtractedCase:
    text = clean_text(f"{title}. {description}")
    number_match = ICC_CASE_NUMBER_RE.search(text)
    parties_match = ICC_PARTIES_RE.search(text)
    claim_match = (
        ICC_CLAIM_RE.search(text)
        or ICC_CHARGES_RE.search(text)
        or ICC_CONVICTION_CLAIM_RE.search(text)
    )
    # La descripción oficial suele expresar el resultado con más precisión
    # que el encabezado; se prioriza sin resumirla ni completar huecos.
    sentences = _sentences(description, title)
    outcome_sentence = next(
        (sentence for sentence in sentences if ICC_OUTCOME_RE.search(sentence)), ""
    )
    status_sentence = next(
        (
            sentence
            for sentence in sentences
            if EXPLICIT_STATUS_RE.search(sentence)
            or ICC_REPARATIONS_COMPLETION_RE.search(sentence)
        ),
        "",
    )
    amount = _explicit_amount(text)

    number = clean_text(number_match.group(0)) if number_match else ""
    parties = clean_text(parties_match.group("parties")) if parties_match else ""
    claim = clean_text(claim_match.group("claim")) if claim_match else ""
    evidence: dict[str, str] = {}
    if number:
        evidence["case_number"] = number
    if parties:
        evidence["case_parties"] = parties
    if claim:
        claim_evidence = next(
            (
                sentence
                for sentence in sentences
                if claim in sentence
                and re.search(
                    r"\b(?:concerns?|involves?|relates?\s+to|charges?|convicted\s+for)\b",
                    sentence,
                    re.I,
                )
            ),
            clean_text(claim_match.group(0)),
        )
        evidence["litis"] = claim_evidence
    if status_sentence:
        evidence["status"] = status_sentence
    if outcome_sentence:
        evidence["outcome"] = outcome_sentence
    if amount:
        evidence["amount"] = amount
    return ExtractedCase(
        number=number,
        parties=parties,
        claim=claim,
        status=status_sentence,
        outcome=outcome_sentence,
        amount=amount,
        evidence=evidence,
    )


def _sentences(*values: str) -> list[str]:
    result: list[str] = []
    for value in values:
        for sentence in re.split(
            r"(?<!\bv\.)(?<!\bV\.)(?<=[.!?])\s+",
            clean_text(value),
        ):
            sentence = sentence.strip()
            if sentence and sentence not in result:
                result.append(sentence)
    return result


def _explicit_amount(text: str) -> str:
    match = AMOUNT_RE.search(text)
    return clean_text(match.group(0)) if match else ""


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
