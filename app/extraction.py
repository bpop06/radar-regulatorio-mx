from __future__ import annotations

import csv
import hashlib
import io
import ipaddress
import json
import mimetypes
import os
import posixpath
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import unicodedata
import zipfile
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpcore
import httpx
from bs4 import BeautifulSoup, Tag
from httpcore._backends.sync import SyncBackend
from httpx._transports.default import HTTPTransport

from app.url_policy import (
    OfficialUrlError,
    redirect_target_allowed,
    resolve_official_link,
    resolve_public_addresses,
    validate_official_url,
)

EXTRACTOR_VERSION = "document-tools-v2"
MAX_WORKERS = 3
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_TOOL_OUTPUT_BYTES = 50 * 1024 * 1024
SUPPORTED_OFFICE_EXTENSIONS = {
    ".doc",
    ".docx",
    ".docm",
    ".ppt",
    ".pptx",
    ".pptm",
    ".xls",
    ".xlsx",
    ".xlsm",
    ".xlsb",
    ".odt",
    ".ods",
    ".odp",
    ".rtf",
    ".epub",
    ".csv",
}
VERIFIABLE_OFFICE_EXTENSIONS = {".docx", ".pptx", ".xlsx", ".odt", ".ods", ".odp", ".csv"}
MAX_OFFICE_XML_MEMBER_BYTES = 16 * 1024 * 1024
MAX_OFFICE_COVERAGE_ANCHORS = 10_000
MAX_OFFICE_ARCHIVE_ENTRIES = 5_000
MAX_OFFICE_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_OFFICE_COMPRESSION_RATIO = 200
HTML_ADAPTERS = {
    "dof.gob.mx": "_extract_dof_html",
    "www.dof.gob.mx": "_extract_dof_html",
    "sidof.segob.gob.mx": "_extract_dof_html",
}
DOF_ANNEX_HOSTS = {"dof.gob.mx", "www.dof.gob.mx", "sidof.segob.gob.mx"}
MAX_DOF_ANNEXES = 20


def _redirect_host_allowed(original_url: str, target_url: str) -> bool:
    original = (urlsplit(original_url).hostname or "").casefold()
    if original in DOF_ANNEX_HOSTS:
        return redirect_target_allowed(
            original_url, target_url, allowed_hosts=DOF_ANNEX_HOSTS
        )
    return redirect_target_allowed(original_url, target_url, allowed_hosts={original})


class _PinnedDNSBackend(SyncBackend):
    """Conecta sólo a las IP públicas resueltas y validadas en ese salto."""

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> Any:
        addresses = _resolved_public_addresses(host, port)
        last_error: Exception | None = None
        for address in addresses:
            try:
                return super().connect_tcp(
                    str(address), port, timeout, local_address, socket_options
                )
            except httpcore.ConnectError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise ExtractionError("el host oficial no tiene IP pública validada")


class _PinnedHTTPTransport(HTTPTransport):
    def __init__(self) -> None:
        super().__init__(trust_env=False, retries=0)
        self._pool = httpcore.ConnectionPool(
            ssl_context=self._pool._ssl_context,  # noqa: SLF001
            max_connections=3,
            max_keepalive_connections=0,
            network_backend=_PinnedDNSBackend(),
        )


@dataclass(frozen=True)
class ExtractionRequest:
    document_id: str
    url: str
    evidence_hash: str = ""
    force_refresh: bool = False


@dataclass(frozen=True)
class ExtractionResult:
    document_id: str
    source_url: str
    status: str
    markdown: str = ""
    content_hash: str = ""
    media_type: str = ""
    extraction_method: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)
    cache_hit: bool = False
    error: str | None = None
    duration_seconds: float = 0.0
    retrieved_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExtractionError(RuntimeError):
    pass


class DocumentExtractor:
    """Descarga y extrae documentos oficiales sin servicios alojados.

    El cache es duck-typed para evitar acoplar esta capa a SQLite: cuando se
    proporciona debe exponer ``get_extraction(url, version, evidence_hash)`` y
    ``put_extraction(result, version, evidence_hash)``. Si además expone
    ``put_extractions``, los resultados paralelos se persisten en un lote.
    """

    def __init__(
        self,
        *,
        cache: Any | None = None,
        timeout: float = 45,
        max_download_bytes: int = MAX_DOWNLOAD_BYTES,
        user_agent: str = "RadarRegulatorioMX/0.1",
    ) -> None:
        self.cache = cache
        self.timeout = timeout
        self.max_download_bytes = max_download_bytes
        self.user_agent = user_agent
        self._thread_state = threading.local()
        self._temporary_budget_lock = threading.Lock()
        self._reserved_temporary_bytes = 0

    def extract_many(
        self, requests: Iterable[ExtractionRequest], *, max_workers: int = MAX_WORKERS
    ) -> list[ExtractionResult]:
        workers = max(1, min(int(max_workers), MAX_WORKERS))
        values = list(requests)
        results: list[ExtractionResult | None] = [None] * len(values)
        missing_by_key: dict[str, list[tuple[int, ExtractionRequest]]] = {}
        for index, request in enumerate(values):
            cached = self._cached(request)
            if cached is None:
                missing_by_key.setdefault(request.url, []).append((index, request))
            else:
                results[index] = cached
        representatives = [group[0] for group in missing_by_key.values()]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            uncached = list(
                pool.map(self._extract_safely, (request for _, request in representatives))
            )
        pending_stores: list[tuple[ExtractionRequest, ExtractionResult]] = []
        for (_, representative), result in zip(representatives, uncached, strict=True):
            group = missing_by_key[representative.url]
            for index, request in group:
                results[index] = _for_request(result, request)
                # Cada alias puede traer un hash de metadatos distinto. La
                # descarga/extracción es única por URL, pero todos los aliases
                # deben quedar enlazados al mismo SHA de bytes en la caché.
                pending_stores.append((request, result))
        self._store_many(pending_stores)
        return [result for result in results if result is not None]

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        cached = self._cached(request)
        if cached is not None:
            return cached
        result = self._extract_safely(request)
        self._store(request, result)
        return result

    def _extract_safely(self, request: ExtractionRequest) -> ExtractionResult:
        started = time.monotonic()
        try:
            result = self._extract_uncached(request)
        except Exception as exc:  # cada fuente falla de forma aislada
            result = ExtractionResult(
                document_id=request.document_id,
                source_url=request.url,
                status="failed",
                error=str(exc),
            )
        values = result.to_dict()
        validators = getattr(self._thread_state, "validators", {})
        diagnostics = dict(values.get("diagnostics") or {})
        if validators and "http_validators" not in diagnostics:
            diagnostics["http_validators"] = validators
            values["diagnostics"] = diagnostics
        self._thread_state.validators = {}
        values["duration_seconds"] = time.monotonic() - started
        values["retrieved_at"] = datetime.now(UTC).isoformat()
        return ExtractionResult(**values)

    def source_is_unchanged(
        self,
        request: ExtractionRequest,
        cached_result: ExtractionResult | None = None,
    ) -> bool:
        """Revalida bytes sin descargar el cuerpo cuando el servidor lo permite."""
        cached = cached_result.to_dict() if cached_result is not None else None
        if cached is None:
            cached = (
                self.cache.get_extraction(
                    request.url, EXTRACTOR_VERSION, request.evidence_hash
                )
                if self.cache is not None
                else None
            )
            if cached is None and self.cache is not None and hasattr(
                self.cache, "get_latest_extraction"
            ):
                cached = self.cache.get_latest_extraction(request.url, EXTRACTOR_VERSION)
        validators = (
            cached.get("diagnostics", {}).get("http_validators", {})
            if isinstance(cached, dict)
            else {}
        )
        # Una publicación compuesta no puede revalidarse con el ETag del
        # cuerpo principal: un anexo puede cambiar de manera independiente.
        if isinstance(cached, dict) and cached.get("diagnostics", {}).get(
            "annex_links"
        ):
            return False
        expected_etag = str(validators.get("etag") or "").strip()
        expected_url = str(validators.get("resolved_url") or request.url)
        # Sólo un ETag fuerte puede autorizar reuso sin volver a hashear el
        # cuerpo. Last-Modified y Content-Length no distinguen cambios de
        # bytes dentro de la misma granularidad/tamaño.
        if not expected_etag or expected_etag.startswith("W/"):
            return False
        try:
            current_url = request.url
            with httpx.Client(
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
                follow_redirects=False,
                trust_env=False,
                transport=_PinnedHTTPTransport(),
            ) as client:
                for _ in range(6):
                    _validate_remote_url(urlsplit(current_url), resolve_dns=True)
                    response = client.head(current_url)
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            return False
                        target_url = urljoin(current_url, location)
                        if not _redirect_host_allowed(request.url, target_url):
                            return False
                        current_url = target_url
                        continue
                    response.raise_for_status()
                    return (
                        current_url == expected_url
                        and response.headers.get("etag", "").strip() == expected_etag
                    )
        except (httpx.HTTPError, ExtractionError, OSError, ValueError):
            return False
        return False

    def cached_source(self, request: ExtractionRequest) -> ExtractionResult | None:
        if self.cache is None or not hasattr(self.cache, "get_latest_extraction"):
            return None
        cached = self.cache.get_latest_extraction(request.url, EXTRACTOR_VERSION)
        if not isinstance(cached, dict) or cached.get("status") != "complete":
            return None
        cached["cache_hit"] = True
        return _for_request(ExtractionResult(**cached), request)

    def _cached(self, request: ExtractionRequest) -> ExtractionResult | None:
        if request.force_refresh:
            return None
        cached = (
            self.cache.get_extraction(request.url, EXTRACTOR_VERSION, request.evidence_hash)
            if self.cache is not None
            else None
        )
        if not isinstance(cached, dict) or cached.get("status") != "complete":
            return None
        cached["cache_hit"] = True
        return _for_request(ExtractionResult(**cached), request)

    def _store(self, request: ExtractionRequest, result: ExtractionResult) -> None:
        if self.cache is not None:
            self.cache.put_extraction(
                result.to_dict(), EXTRACTOR_VERSION, request.evidence_hash
            )

    def _store_many(
        self, entries: Iterable[tuple[ExtractionRequest, ExtractionResult]]
    ) -> None:
        values = list(entries)
        if self.cache is None or not values:
            return
        if hasattr(self.cache, "put_extractions"):
            self.cache.put_extractions(
                [
                    (result.to_dict(), EXTRACTOR_VERSION, request.evidence_hash)
                    for request, result in values
                ]
            )
            return
        for request, result in values:
            self._store(request, result)

    def _extract_uncached(self, request: ExtractionRequest) -> ExtractionResult:
        reservation = self._reserve_temporary_space()
        try:
            with tempfile.TemporaryDirectory(prefix="radar-extract-") as temporary:
                directory = Path(temporary)
                downloaded, media_type, digest = self._download(request.url, directory)
                suffix = downloaded.suffix.lower()
                if media_type == "application/pdf" or suffix == ".pdf":
                    return self._extract_pdf(
                        request, downloaded, media_type, digest, directory
                    )
                if suffix in SUPPORTED_OFFICE_EXTENSIONS:
                    return self._extract_office(
                        request, downloaded, media_type, digest, directory
                    )
                if media_type in {"text/html", "application/xhtml+xml"} or suffix in {
                    ".html",
                    ".htm",
                    ".php",
                    "",
                }:
                    adapter_name = HTML_ADAPTERS.get(
                        (urlsplit(request.url).hostname or "").casefold(),
                        "_extract_generic_html",
                    )
                    return getattr(self, adapter_name)(
                        request, downloaded, media_type, digest
                    )
                return ExtractionResult(
                    document_id=request.document_id,
                    source_url=request.url,
                    status="unsupported",
                    content_hash=digest,
                    media_type=media_type,
                    error=f"formato no soportado: {media_type or suffix or 'desconocido'}",
                )
        finally:
            self._release_temporary_space(reservation)

    def _reserve_temporary_space(self) -> int:
        """Reserve worst-case disk space per worker before it downloads bytes."""

        reservation = self.max_download_bytes + MAX_TOOL_OUTPUT_BYTES
        with self._temporary_budget_lock:
            available = shutil.disk_usage(tempfile.gettempdir()).free
            if available < self._reserved_temporary_bytes + reservation:
                raise ExtractionError(
                    "espacio temporal insuficiente para una extracción verificable"
                )
            self._reserved_temporary_bytes += reservation
        return reservation

    def _release_temporary_space(self, reservation: int) -> None:
        with self._temporary_budget_lock:
            self._reserved_temporary_bytes = max(
                0, self._reserved_temporary_bytes - reservation
            )

    def _download(self, url: str, directory: Path) -> tuple[Path, str, str]:
        current_url = url
        with httpx.Client(
            follow_redirects=False,
            timeout=self.timeout,
            headers={"User-Agent": self.user_agent, "Accept": "*/*"},
            trust_env=False,
            transport=_PinnedHTTPTransport(),
        ) as client:
            for _ in range(6):
                parts = urlsplit(current_url)
                _validate_remote_url(parts, resolve_dns=True)
                suffix = Path(parts.path).suffix.lower()
                with client.stream("GET", current_url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise ExtractionError("redirección oficial sin destino")
                        target_url = urljoin(current_url, location)
                        if not _redirect_host_allowed(url, target_url):
                            raise ExtractionError(
                                "redirección fuera del origen oficial permitido"
                            )
                        current_url = target_url
                        continue
                    response.raise_for_status()
                    media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if not suffix:
                        suffix = mimetypes.guess_extension(media_type) or ".html"
                    target = directory / f"source{suffix}"
                    digest = hashlib.sha256()
                    size = 0
                    with target.open("wb") as stream:
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > self.max_download_bytes:
                                raise ExtractionError("el documento excede el límite de descarga")
                            digest.update(chunk)
                            stream.write(chunk)
                    self._thread_state.validators = {
                        key: response.headers.get(key)
                        for key in ("etag", "last-modified", "content-length")
                        if response.headers.get(key)
                    }
                    self._thread_state.validators["resolved_url"] = current_url
                    return target, media_type, digest.hexdigest()
        raise ExtractionError("demasiadas redirecciones en la fuente oficial")

    def _extract_pdf(
        self,
        request: ExtractionRequest,
        source: Path,
        media_type: str,
        digest: str,
        directory: Path,
    ) -> ExtractionResult:
        detector = _require_command("pdf-inspector")
        detection = _run_json([detector, "detect", str(source), "--json"], self.timeout)
        original_detection = dict(detection)
        pdf_type = detection.get("pdfType")
        pending = detection.get("pagesNeedingOcr") or []
        extracted_source = source
        method = "pdf-inspector"
        if pdf_type != "TextBased" or pending:
            ocr = _require_command("ocrmypdf")
            ocr_output = directory / "source-ocr.pdf"
            command = [
                ocr,
                "--language",
                "spa+eng",
                "--deskew",
                "--rotate-pages",
                "--skip-text",
                str(source),
                str(ocr_output),
            ]
            completed = _run(command, self.timeout * 4)
            if completed.returncode != 0 or not ocr_output.exists():
                return ExtractionResult(
                    document_id=request.document_id,
                    source_url=request.url,
                    status="needs_ocr",
                    content_hash=digest,
                    media_type=media_type or "application/pdf",
                    extraction_method="pdf-inspector-detect",
                    diagnostics={"detection": original_detection},
                    error=_bounded_error(completed.stderr or "OCR local falló"),
                )
            _assert_tool_output_size(ocr_output)
            detection = _run_json(
                [detector, "detect", str(ocr_output), "--json"], self.timeout
            )
            extracted_source = ocr_output
            method = "ocrmypdf+pdf-inspector"

        if detection.get("pdfType") != "TextBased" or detection.get("pagesNeedingOcr"):
            return ExtractionResult(
                document_id=request.document_id,
                source_url=request.url,
                status="partial",
                content_hash=digest,
                media_type=media_type or "application/pdf",
                extraction_method=method,
                diagnostics={
                    "detection": original_detection,
                    "post_ocr_detection": detection,
                },
                error="la detección final todavía reporta páginas sin texto",
            )

        extraction = _run_json([detector, str(extracted_source), "--json"], self.timeout * 2)
        markdown = str(extraction.get("markdown") or "").strip()
        if not markdown:
            raise ExtractionError("pdf-inspector devolvió una extracción vacía")
        extracted_pending = extraction.get("pagesNeedingOcr") or []
        original_pages = original_detection.get("pageCount")
        detected_pages = detection.get("pageCount")
        extracted_pages = extraction.get("pageCount")
        page_counts_valid = (
            isinstance(original_pages, int)
            and not isinstance(original_pages, bool)
            and original_pages > 0
            and
            isinstance(detected_pages, int)
            and not isinstance(detected_pages, bool)
            and detected_pages > 0
            and detected_pages == original_pages
            and isinstance(extracted_pages, int)
            and not isinstance(extracted_pages, bool)
            and extracted_pages == detected_pages
        )
        if extracted_pending or not page_counts_valid or extraction.get("hasEncodingIssues"):
            return ExtractionResult(
                document_id=request.document_id,
                source_url=request.url,
                status="partial",
                content_hash=digest,
                media_type=media_type or "application/pdf",
                extraction_method=method,
                diagnostics={"detection": detection, "extraction": extraction},
                error="la extracción PDF no cubre todas las páginas detectadas",
            )
        pages_with_tables = extraction.get("pagesWithTables") or []
        if pages_with_tables and _markdown_table_count(markdown) < len(
            set(pages_with_tables)
        ):
            return ExtractionResult(
                document_id=request.document_id,
                source_url=request.url,
                status="partial",
                markdown=markdown,
                content_hash=digest,
                media_type=media_type or "application/pdf",
                extraction_method=method,
                diagnostics={"detection": detection, "extraction": extraction},
                error="la extracción PDF no conservó todas las tablas detectadas",
            )
        diagnostics = {
            "detection": original_detection,
            "post_ocr_detection": detection,
            "page_count": extraction.get("pageCount") or detection.get("pageCount"),
            "pages_with_tables": pages_with_tables,
            "pages_with_columns": extraction.get("pagesWithColumns") or [],
            "complex_layout": bool(extraction.get("isComplexLayout")),
            "encoding_issues": bool(extraction.get("hasEncodingIssues")),
            "beginning_sample": markdown[:300],
            "ending_sample": markdown[-300:],
        }
        return ExtractionResult(
            document_id=request.document_id,
            source_url=request.url,
            status="complete",
            markdown=markdown,
            content_hash=digest,
            media_type=media_type or "application/pdf",
            extraction_method=method,
            diagnostics=diagnostics,
        )

    def _extract_office(
        self,
        request: ExtractionRequest,
        source: Path,
        media_type: str,
        digest: str,
        directory: Path,
    ) -> ExtractionResult:
        suffix = source.suffix.casefold()
        with source.open("rb") as stream:
            archive_magic = stream.read(4)
        if archive_magic.startswith(b"PK"):
            _preflight_office_archive(source)
        anydoc = _require_command("anydoc")
        output = directory / "source.md"
        completed = _run([anydoc, str(source), "-o", str(output)], self.timeout * 2)
        if completed.returncode != 0 or not output.exists():
            raise ExtractionError(_bounded_error(completed.stderr or "AnyDoc falló"))
        _assert_tool_output_size(output)
        markdown = output.read_text(encoding="utf-8").strip()
        if not markdown:
            raise ExtractionError("AnyDoc devolvió una extracción vacía")
        diagnostics: dict[str, Any] = {
            "beginning_sample": markdown[:300],
            "ending_sample": markdown[-300:],
            "source_size_bytes": source.stat().st_size,
            "format": suffix.lstrip("."),
        }
        coverage, coverage_error = _verify_anydoc_coverage(source, suffix, markdown)
        diagnostics["coverage"] = coverage
        status = "complete" if coverage["verified"] else "partial"
        return ExtractionResult(
            document_id=request.document_id,
            source_url=request.url,
            status=status,
            markdown=markdown,
            content_hash=digest,
            media_type=media_type,
            extraction_method="anydoc",
            diagnostics=diagnostics,
            error=(
                None
                if status == "complete"
                else coverage_error or "AnyDoc no conservó cobertura verificable del documento"
            ),
        )

    def _extract_html(
        self, request: ExtractionRequest, source: Path, media_type: str, digest: str
    ) -> ExtractionResult:
        """Alias estable para fixtures de parser existentes."""
        adapter_name = HTML_ADAPTERS.get(
            (urlsplit(request.url).hostname or "").casefold(), "_extract_generic_html"
        )
        return getattr(self, adapter_name)(request, source, media_type, digest)

    def _extract_dof_html(
        self, request: ExtractionRequest, source: Path, media_type: str, digest: str
    ) -> ExtractionResult:
        html = _read_html_text(source)
        soup = BeautifulSoup(html, "html.parser")
        root = soup.select_one("#DivDetalleNota")
        if root is None:
            raise ExtractionError("DOF no contiene #DivDetalleNota")
        result = self._html_result(request, root, media_type, digest, "#DivDetalleNota")
        diagnostics = dict(result.diagnostics)
        main_validators = dict(getattr(self._thread_state, "validators", {}) or {})
        if main_validators:
            diagnostics["http_validators"] = main_validators
        candidates: list[str] = []
        rejected_annexes: list[str] = []
        for link in root.select("a[href], iframe[src], object[data], embed[src]"):
            attribute = (
                "href" if link.name == "a" else "data" if link.name == "object" else "src"
            )
            value = str(link.get(attribute) or "")
            label = " ".join(link.get_text(" ", strip=True).split())
            is_embed = link.name in {"iframe", "object", "embed"}
            is_named_attachment = bool(
                re.search(
                    r"anexo|adjunto|nota_detalle_popup|"
                    r"\.(?:pdf|docx?|xlsx?|pptx?|ods|odt|odp|csv)(?:$|\?)",
                    f"{value} {label}",
                    re.I,
                )
            )
            if value and (is_embed or is_named_attachment):
                try:
                    candidates.append(
                        resolve_official_link(
                            request.url,
                            value,
                            allowed_hosts=DOF_ANNEX_HOSTS,
                        )
                    )
                except OfficialUrlError:
                    rejected_annexes.append(value)
        candidates = sorted(set(candidates) - {request.url})
        annexes = candidates
        rejected_annexes = sorted(set(rejected_annexes))
        if rejected_annexes or len(annexes) > MAX_DOF_ANNEXES:
            diagnostics["annex_links"] = annexes[:MAX_DOF_ANNEXES]
            diagnostics["rejected_annex_links"] = rejected_annexes
            return ExtractionResult(
                **{
                    **result.to_dict(),
                    "status": "partial",
                    "diagnostics": diagnostics,
                    "error": "el DOF enlaza anexos fuera del origen permitido o excede el límite",
                }
            )
        diagnostics["annex_links"] = annexes
        annex_results: list[dict[str, Any]] = []
        markdown_parts = [result.markdown]
        hash_parts = [digest]
        total_annex_bytes = 0
        for index, annex_url in enumerate(annexes, start=1):
            annex_directory = source.parent / f"annex-{index}"
            annex_directory.mkdir(exist_ok=True)
            try:
                downloaded, annex_media, annex_hash = self._download(
                    annex_url, annex_directory
                )
                total_annex_bytes += downloaded.stat().st_size
                if total_annex_bytes > self.max_download_bytes:
                    raise ExtractionError("los anexos exceden el límite total de descarga")
                annex_request = ExtractionRequest(
                    f"{request.document_id}:annex:{index}", annex_url
                )
                annex = self._extract_annex(
                    annex_request,
                    downloaded,
                    annex_media,
                    annex_hash,
                    annex_directory,
                )
            except Exception as exc:
                annex = ExtractionResult(
                    f"{request.document_id}:annex:{index}",
                    annex_url,
                    "failed",
                    error=str(exc),
                )
            annex_results.append(
                {
                    "url": annex_url,
                    "status": annex.status,
                    "hash": annex.content_hash,
                    "method": annex.extraction_method,
                    "diagnostics": annex.diagnostics,
                    "error": annex.error,
                }
            )
            if annex.status != "complete":
                diagnostics["annexes"] = annex_results
                return ExtractionResult(
                    **{
                        **result.to_dict(),
                        "status": "partial",
                        "diagnostics": diagnostics,
                        "error": "un anexo DOF no pudo extraerse íntegramente",
                    }
                )
            markdown_parts.append(
                f"# Anexo {index}\n\nFuente: {annex_url}\n\n{annex.markdown}"
            )
            hash_parts.extend((annex_url, annex.content_hash))
        diagnostics["annexes"] = annex_results
        combined_hash = hashlib.sha256("\0".join(hash_parts).encode()).hexdigest()
        return ExtractionResult(
            **{
                **result.to_dict(),
                "markdown": "\n\n".join(markdown_parts),
                "content_hash": combined_hash,
                "diagnostics": diagnostics,
            }
        )

    def _extract_annex(
        self,
        request: ExtractionRequest,
        source: Path,
        media_type: str,
        digest: str,
        directory: Path,
    ) -> ExtractionResult:
        suffix = source.suffix.casefold()
        if media_type == "application/pdf" or suffix == ".pdf":
            return self._extract_pdf(request, source, media_type, digest, directory)
        if suffix in SUPPORTED_OFFICE_EXTENSIONS:
            return self._extract_office(request, source, media_type, digest, directory)
        if media_type in {"text/html", "application/xhtml+xml"} or suffix in {
            ".html", ".htm", ".php", "",
        }:
            return self._extract_generic_html(request, source, media_type, digest)
        return ExtractionResult(
            request.document_id,
            request.url,
            "unsupported",
            content_hash=digest,
            media_type=media_type,
            error="formato de anexo DOF no soportado",
        )

    def _extract_generic_html(
        self, request: ExtractionRequest, source: Path, media_type: str, digest: str
    ) -> ExtractionResult:
        html = _read_html_text(source)
        soup = BeautifulSoup(html, "html.parser")
        for node in soup.select("script, style, noscript, nav, footer"):
            node.decompose()
        root = soup.select_one("#DivDetalleNota") or soup.select_one("main, article") or soup.body
        if root is None:
            raise ExtractionError("la página no contiene un cuerpo reconocible")
        selector = "main/article/body"
        result = self._html_result(request, root, media_type, digest, selector)
        material = list(root.select("iframe[src], object[data], embed[src]"))
        for link in root.select("a[href]"):
            href = str(link.get("href") or "")
            label = " ".join(link.get_text(" ", strip=True).split())
            if re.search(
                r"anexo|adjunto|\.(?:pdf|docx?|xlsx?|pptx?|ods|odt|odp|csv)(?:$|\?)",
                f"{href} {label}",
                re.I,
            ):
                material.append(link)
        if material:
            return ExtractionResult(
                **{
                    **result.to_dict(),
                    "status": "partial",
                    "error": "la fuente HTML enlaza documentos sin adaptador verificable",
                }
            )
        return result

    def _html_result(
        self,
        request: ExtractionRequest,
        root: Tag,
        media_type: str,
        digest: str,
        selector: str,
    ) -> ExtractionResult:
        markdown = _html_to_markdown(root).strip()
        if len(markdown) < 80:
            raise ExtractionError("el cuerpo HTML extraído es demasiado corto")
        source_tables = len(
            [table for table in root.select("table") if not table.find_parent("table")]
        )
        if _markdown_table_count(markdown) < source_tables:
            return ExtractionResult(
                document_id=request.document_id,
                source_url=request.url,
                status="partial",
                markdown=markdown,
                content_hash=digest,
                media_type=media_type or "text/html",
                extraction_method="html-adapter",
                diagnostics={"selector": selector, "tables": source_tables},
                error="la extracción HTML no conservó todas las tablas",
            )
        output_text = _normalise_coverage_text(markdown)
        # El destino del enlace se verifica como anexo por separado; su texto
        # visible no forma parte del cuerpo normativo que convierte este paso.
        coverage_root = BeautifulSoup(str(root), "html.parser")
        coverage_scope = coverage_root.select_one("#DivDetalleNota > html > body")
        if coverage_scope is None:
            coverage_scope = coverage_root.select_one("body") or coverage_root
        for link in coverage_root.select("a, iframe, object, embed"):
            link.extract()
        source_text = _normalise_coverage_text(coverage_scope.get_text(" ", strip=True))
        cursor = 0
        for anchor in _coverage_anchors([source_text]):
            position = output_text.find(anchor, cursor)
            if position < 0:
                return ExtractionResult(
                    document_id=request.document_id,
                    source_url=request.url,
                    status="partial",
                    markdown=markdown,
                    content_hash=digest,
                    media_type=media_type or "text/html",
                    extraction_method="html-adapter",
                    diagnostics={"selector": selector, "tables": source_tables},
                    error="la extracción HTML omitió texto material del cuerpo",
                )
            cursor = position + len(anchor)
        return ExtractionResult(
            document_id=request.document_id,
            source_url=request.url,
            status="complete",
            markdown=markdown,
            content_hash=digest,
            media_type=media_type or "text/html",
            extraction_method="html-adapter",
            diagnostics={
                "selector": selector,
                "tables": source_tables,
                "beginning_sample": markdown[:300],
                "ending_sample": markdown[-300:],
            },
        )


def _html_to_markdown(root: Tag) -> str:
    blocks: list[str] = []
    # El DOF inserta un segundo árbol <html><body> dentro de DivDetalleNota.
    # BeautifulSoup lo tolera, pero el filtro de ancestros no debe tratar ese
    # wrapper como contenido anidado material.
    scope = root.select_one(":scope > html > body") or root
    supported = [
        "h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table",
        "blockquote", "pre", "dt", "dd", "caption", "div",
    ]
    for node in scope.find_all(supported):
        parent = node.find_parent(["p", "li", "table", "blockquote", "pre"])
        if parent is not None and parent is not scope and scope in parent.parents:
            continue
        if node.name == "div" and node.find(supported, recursive=False) is not None:
            continue
        text = " ".join(node.get_text(" ", strip=True).split())
        if not text:
            continue
        if node.name and node.name.startswith("h"):
            level = min(int(node.name[1]), 6)
            blocks.append(f"{'#' * level} {text}")
        elif node.name == "li":
            blocks.append(f"- {text}")
        elif node.name == "blockquote":
            blocks.append(f"> {text}")
        elif node.name == "pre":
            blocks.append(f"```text\n{text}\n```")
        elif node.name == "table":
            rows = [
                [
                    " ".join(cell.get_text(" ", strip=True).split())
                    for cell in row.find_all(["th", "td"])
                ]
                for row in node.find_all("tr")
            ]
            rows = [row for row in rows if row]
            if rows:
                width = max(len(row) for row in rows)
                normalized = [row + [""] * (width - len(row)) for row in rows]
                blocks.append("| " + " | ".join(normalized[0]) + " |")
                blocks.append("| " + " | ".join(["---"] * width) + " |")
                blocks.extend("| " + " | ".join(row) + " |" for row in normalized[1:])
        else:
            blocks.append(text)
    return "\n\n".join(blocks)


def _read_html_text(source: Path) -> str:
    raw = source.read_bytes()
    match = re.search(
        br"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", raw[:4096], re.I
    )
    encodings = [
        match.group(1).decode("ascii", errors="strict") if match else "utf-8",
    ]
    if encodings[0].casefold() != "utf-8":
        encodings.append("utf-8")
    for encoding in encodings:
        try:
            text = raw.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
        if "\ufffd" not in text:
            return text
    raise ExtractionError("el HTML contiene bytes o charset no verificables")


class _OfficeCoverageError(ValueError):
    """La fuente no permite probar localmente que AnyDoc la cubrió íntegra."""


def _preflight_office_archive(source: Path) -> None:
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ExtractionError("contenedor Office inválido") from exc
    total = sum(entry.file_size for entry in entries)
    if len(entries) > MAX_OFFICE_ARCHIVE_ENTRIES or total > MAX_OFFICE_UNCOMPRESSED_BYTES:
        raise ExtractionError("contenedor Office excede límites estructurales")
    if any(
        entry.file_size > MAX_OFFICE_XML_MEMBER_BYTES
        or entry.file_size / max(1, entry.compress_size) > MAX_OFFICE_COMPRESSION_RATIO
        for entry in entries
    ):
        raise ExtractionError("contenedor Office tiene compresión o miembros inseguros")


def _verify_anydoc_coverage(
    source: Path, suffix: str, markdown: str
) -> tuple[dict[str, Any], str | None]:
    """Compara el Markdown de AnyDoc con una huella estructural de la fuente.

    AnyDoc no entrega un manifiesto de páginas, hojas o diapositivas. Por eso
    sólo aceptamos los formatos cuyo contenedor se puede inspeccionar de forma
    local con la biblioteca estándar; cualquier formato que no permita esta
    comprobación se retiene como ``partial``.
    """
    if suffix not in VERIFIABLE_OFFICE_EXTENSIONS:
        return (
            {
                "verified": False,
                "reason": "formato sin inventario estructural verificable",
                "format": suffix.lstrip("."),
            },
            "AnyDoc no permite verificar íntegramente este formato binario",
        )
    try:
        inventory = _office_source_inventory(source, suffix)
        return _compare_anydoc_coverage(inventory, markdown)
    except (
        OSError,
        UnicodeError,
        csv.Error,
        ElementTree.ParseError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        _OfficeCoverageError,
    ) as exc:
        return (
            {
                "verified": False,
                "reason": _bounded_error(str(exc)),
                "format": suffix.lstrip("."),
            },
            "no se pudo verificar localmente la cobertura estructural de AnyDoc",
        )


def _office_source_inventory(source: Path, suffix: str) -> dict[str, Any]:
    if suffix == ".csv":
        return _csv_source_inventory(source)
    with zipfile.ZipFile(source) as archive:
        if suffix == ".docx":
            return _docx_source_inventory(archive)
        if suffix == ".pptx":
            return _pptx_source_inventory(archive)
        if suffix == ".xlsx":
            return _xlsx_source_inventory(archive)
        if suffix == ".odt":
            return _odt_source_inventory(archive)
        if suffix == ".ods":
            return _ods_source_inventory(archive)
        if suffix == ".odp":
            return _odp_source_inventory(archive)
    raise _OfficeCoverageError("formato Office no verificable")


def _docx_source_inventory(archive: zipfile.ZipFile) -> dict[str, Any]:
    document = _zip_xml(archive, "word/document.xml")
    body = next((node for node in document.iter() if _xml_local_name(node) == "body"), None)
    if body is None:
        raise _OfficeCoverageError("DOCX sin cuerpo de documento")
    blocks: list[str] = []
    tables: list[list[str]] = []
    ordered: list[str] = []
    for child in list(body):
        if _xml_local_name(child) == "p":
            text = _xml_text(child)
            if text:
                blocks.append(text)
                ordered.append(text)
        elif _xml_local_name(child) == "tbl":
            rows = _word_table_rows(child)
            if rows:
                tables.append(rows)
                ordered.extend(rows)
    units = [
        {"label": "Documento", "blocks": blocks, "tables": tables, "ordered": ordered}
    ]
    story_pattern = re.compile(
        r"^word/(?:footnotes|endnotes|comments|header\d+|footer\d+)\.xml$"
    )
    for name in sorted(archive.namelist()):
        if not story_pattern.match(name):
            continue
        story = _zip_xml(archive, name)
        story_blocks = _xml_textual_blocks(story, {"p"})
        story_tables = [
            _word_table_rows(table)
            for table in story.iter()
            if _xml_local_name(table) == "tbl"
        ]
        story_tables = [rows for rows in story_tables if rows]
        story_ordered = [*story_blocks, *(row for rows in story_tables for row in rows)]
        if story_ordered:
            units.append(
                {
                    "label": Path(name).stem,
                    "blocks": story_blocks,
                    "tables": story_tables,
                    "ordered": story_ordered,
                }
            )
    return _coverage_inventory("docx", "story", units)


def _pptx_source_inventory(archive: zipfile.ZipFile) -> dict[str, Any]:
    presentation = _zip_xml(archive, "ppt/presentation.xml")
    relationships = _ooxml_relationship_targets(
        archive, "ppt/_rels/presentation.xml.rels", "ppt"
    )
    slide_ids = [node for node in presentation.iter() if _xml_local_name(node) == "sldId"]
    if not slide_ids:
        raise _OfficeCoverageError("PPTX sin diapositivas verificables")
    units: list[dict[str, Any]] = []
    for index, slide_id in enumerate(slide_ids, start=1):
        target = relationships.get(_relationship_id(slide_id))
        if not target:
            raise _OfficeCoverageError("PPTX con relación de diapositiva incompleta")
        slide = _zip_xml(archive, target)
        blocks = _xml_textual_blocks(slide, {"p"})
        tables = [
            _drawing_table_rows(table)
            for table in slide.iter()
            if _xml_local_name(table) == "tbl"
        ]
        tables = [rows for rows in tables if rows]
        ordered = blocks or [row for rows in tables for row in rows]
        units.append(
            {
                "label": f"Diapositiva {index}",
                "blocks": blocks,
                "tables": tables,
                "ordered": ordered,
            }
        )
    return _coverage_inventory("pptx", "slides", units, requires_headings=True)


def _xlsx_source_inventory(archive: zipfile.ZipFile) -> dict[str, Any]:
    workbook = _zip_xml(archive, "xl/workbook.xml")
    relationships = _ooxml_relationship_targets(archive, "xl/_rels/workbook.xml.rels", "xl")
    shared_strings = _xlsx_shared_strings(archive)
    sheets = [node for node in workbook.iter() if _xml_local_name(node) == "sheet"]
    if not sheets:
        raise _OfficeCoverageError("XLSX sin hojas verificables")
    units: list[dict[str, Any]] = []
    for index, sheet in enumerate(sheets, start=1):
        name = _xml_attribute(sheet, "name") or f"Hoja {index}"
        target = relationships.get(_relationship_id(sheet))
        if not target:
            raise _OfficeCoverageError("XLSX con relación de hoja incompleta")
        worksheet = _zip_xml(archive, target)
        rows = _xlsx_sheet_rows(worksheet, shared_strings)
        units.append(
            {
                "label": name,
                "blocks": [],
                "tables": [rows] if rows else [],
                "ordered": rows,
            }
        )
    defined_tables = sum(
        1
        for name in archive.namelist()
        if name.startswith("xl/tables/") and name.lower().endswith(".xml")
    )
    return _coverage_inventory(
        "xlsx",
        "sheets",
        units,
        requires_headings=True,
        requires_labels=True,
        minimum_tables=defined_tables,
    )


def _odt_source_inventory(archive: zipfile.ZipFile) -> dict[str, Any]:
    root = _zip_xml(archive, "content.xml")
    text = next((node for node in root.iter() if _xml_local_name(node) == "text"), None)
    if text is None:
        raise _OfficeCoverageError("ODT sin cuerpo de texto")
    blocks = _xml_textual_blocks(text, {"p", "h"})
    tables = [_odf_table_rows(table) for table in text.iter() if _xml_local_name(table) == "table"]
    tables = [rows for rows in tables if rows]
    ordered = blocks or [row for rows in tables for row in rows]
    return _coverage_inventory(
        "odt",
        "document",
        [{"label": "Documento", "blocks": blocks, "tables": tables, "ordered": ordered}],
    )


def _ods_source_inventory(archive: zipfile.ZipFile) -> dict[str, Any]:
    root = _zip_xml(archive, "content.xml")
    spreadsheet = next(
        (node for node in root.iter() if _xml_local_name(node) == "spreadsheet"), None
    )
    if spreadsheet is None:
        raise _OfficeCoverageError("ODS sin libro de hojas")
    sheets = [node for node in list(spreadsheet) if _xml_local_name(node) == "table"]
    if not sheets:
        raise _OfficeCoverageError("ODS sin hojas verificables")
    units: list[dict[str, Any]] = []
    for index, sheet in enumerate(sheets, start=1):
        rows = _odf_table_rows(sheet)
        units.append(
            {
                "label": _xml_attribute(sheet, "name") or f"Hoja {index}",
                "blocks": [],
                "tables": [rows] if rows else [],
                "ordered": rows,
            }
        )
    return _coverage_inventory(
        "ods", "sheets", units, requires_headings=True, requires_labels=True
    )


def _odp_source_inventory(archive: zipfile.ZipFile) -> dict[str, Any]:
    root = _zip_xml(archive, "content.xml")
    pages = [node for node in root.iter() if _xml_local_name(node) == "page"]
    if not pages:
        raise _OfficeCoverageError("ODP sin diapositivas verificables")
    units: list[dict[str, Any]] = []
    for index, page in enumerate(pages, start=1):
        blocks = _xml_textual_blocks(page, {"p", "h"})
        tables = [
            _odf_table_rows(table)
            for table in page.iter()
            if _xml_local_name(table) == "table"
        ]
        tables = [rows for rows in tables if rows]
        units.append(
            {
                "label": f"Diapositiva {index}",
                "blocks": blocks,
                "tables": tables,
                "ordered": blocks or [row for rows in tables for row in rows],
            }
        )
    return _coverage_inventory("odp", "slides", units, requires_headings=True)


def _csv_source_inventory(source: Path) -> dict[str, Any]:
    raw = source.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    if not text.strip():
        raise _OfficeCoverageError("CSV vacío; no hay cobertura textual verificable")
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [
        " | ".join(cell.strip() for cell in row)
        for row in csv.reader(io.StringIO(text, newline=""), dialect)
        if any(cell.strip() for cell in row)
    ]
    if not rows:
        raise _OfficeCoverageError("CSV sin filas materiales verificables")
    return _coverage_inventory(
        "csv",
        "table",
        [{"label": "CSV", "blocks": [], "tables": [rows], "ordered": rows}],
    )


def _coverage_inventory(
    format_name: str,
    unit_kind: str,
    units: list[dict[str, Any]],
    *,
    requires_headings: bool = False,
    requires_labels: bool = False,
    minimum_tables: int = 0,
) -> dict[str, Any]:
    if not units:
        raise _OfficeCoverageError("la fuente no contiene unidades estructurales")
    source_tables = sum(len(unit["tables"]) for unit in units)
    return {
        "format": format_name,
        "unit_kind": unit_kind,
        "units": units,
        "requires_headings": requires_headings,
        "requires_labels": requires_labels,
        "required_markdown_tables": max(source_tables, minimum_tables),
    }


def _compare_anydoc_coverage(
    inventory: dict[str, Any], markdown: str
) -> tuple[dict[str, Any], str | None]:
    normalized_markdown = _normalise_coverage_text(markdown)
    headings = [
        _normalise_coverage_text(value)
        for value in re.findall(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", markdown)
    ]
    output_tables = _markdown_table_count(markdown)
    units = inventory["units"]
    missing: list[str] = []
    anchors: list[tuple[str, str]] = []
    ordered_source: list[str] = []
    for unit_index, unit in enumerate(units, start=1):
        ordered = [value for value in unit["ordered"] if _normalise_coverage_text(value)]
        ordered_source.extend(ordered)
        for anchor in _coverage_anchors(ordered):
            anchors.append((anchor, f"{inventory['unit_kind']} {unit_index}"))
        for table_index, rows in enumerate(unit["tables"], start=1):
            for anchor in _coverage_anchors(rows):
                anchors.append((anchor, f"tabla {unit_index}.{table_index}"))
    if len(anchors) > MAX_OFFICE_COVERAGE_ANCHORS:
        return (
            {
                "verified": False,
                "format": inventory["format"],
                "reason": "demasiados fragmentos para verificar sin muestreo",
                "source_units": len(units),
                "source_tables": sum(len(unit["tables"]) for unit in units),
            },
            "la fuente excede la cobertura local verificable; se retuvo sin publicar",
        )
    seen: set[str] = set()
    cursor = 0
    for anchor, location in anchors:
        if anchor in seen:
            continue
        seen.add(anchor)
        position = normalized_markdown.find(anchor, cursor)
        if position < 0:
            missing.append(location)
            continue
        cursor = position + len(anchor)
    beginning_verified = False
    ending_verified = False
    if ordered_source:
        beginning = _coverage_anchors([ordered_source[0]])
        ending = _coverage_anchors([ordered_source[-1]])
        beginning_verified = bool(beginning) and all(
            anchor in normalized_markdown for anchor in beginning
        )
        ending_verified = bool(ending) and all(anchor in normalized_markdown for anchor in ending)
        if not beginning_verified:
            missing.append("inicio de la fuente")
        if not ending_verified:
            missing.append("final de la fuente")
    else:
        missing.append("sin contenido textual fuente verificable")

    markers_verified = True
    if inventory["requires_headings"]:
        markers_verified = len(headings) >= len(units)
        if not markers_verified:
            missing.append("marcadores de unidades")
        if inventory["requires_labels"]:
            for unit in units:
                label = _normalise_coverage_text(unit["label"])
                if label and not any(label in heading for heading in headings):
                    markers_verified = False
                    missing.append(f"marcador {unit['label']}")

    required_tables = inventory["required_markdown_tables"]
    tables_verified = output_tables >= required_tables
    if not tables_verified:
        missing.append("tablas de la fuente")
    source_tables = sum(len(unit["tables"]) for unit in units)
    diagnostics = {
        "verified": not missing,
        "format": inventory["format"],
        "source_units": len(units),
        "source_tables": source_tables,
        "required_markdown_tables": required_tables,
        "output_markdown_tables": output_tables,
        "beginning_verified": beginning_verified,
        "ending_verified": ending_verified,
        "unit_markers_verified": markers_verified,
        "tables_verified": tables_verified,
        "missing": missing[:50],
        "missing_count": len(missing),
    }
    if missing:
        return diagnostics, "AnyDoc no cubrió inicio, final o estructura verificable de la fuente"
    return diagnostics, None


def _zip_xml(archive: zipfile.ZipFile, name: str) -> ElementTree.Element:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise _OfficeCoverageError(f"contenedor Office sin {name}") from exc
    if info.file_size > MAX_OFFICE_XML_MEMBER_BYTES:
        raise _OfficeCoverageError(f"miembro Office demasiado grande para verificar: {name}")
    return ElementTree.fromstring(archive.read(name))


def _ooxml_relationship_targets(
    archive: zipfile.ZipFile, relationship_path: str, base_directory: str
) -> dict[str, str]:
    root = _zip_xml(archive, relationship_path)
    targets: dict[str, str] = {}
    for relation in root.iter():
        if _xml_local_name(relation) != "Relationship":
            continue
        if (_xml_attribute(relation, "TargetMode") or "").casefold() == "external":
            continue
        relation_id = _xml_attribute(relation, "Id")
        target = _xml_attribute(relation, "Target")
        if not relation_id or not target:
            continue
        joined = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.join(base_directory, target)
        )
        normalized = posixpath.normpath(joined)
        if normalized.startswith("../"):
            raise _OfficeCoverageError("relación Office fuera del contenedor")
        targets[relation_id] = normalized
    return targets


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = _zip_xml(archive, "xl/sharedStrings.xml")
    return [_xml_text(node) for node in root if _xml_local_name(node) == "si"]


def _xlsx_sheet_rows(root: ElementTree.Element, shared_strings: list[str]) -> list[str]:
    rows: list[str] = []
    for row in root.iter():
        if _xml_local_name(row) != "row":
            continue
        values = [
            _xlsx_cell_text(cell, shared_strings)
            for cell in list(row)
            if _xml_local_name(cell) == "c"
        ]
        material = " | ".join(value for value in values if value)
        if material:
            rows.append(material)
    return rows


def _xlsx_cell_text(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = _xml_attribute(cell, "t")
    value_node = next((node for node in cell if _xml_local_name(node) == "v"), None)
    value = (value_node.text or "").strip() if value_node is not None else ""
    if cell_type == "s":
        try:
            return shared_strings[int(value)]
        except (IndexError, ValueError) as exc:
            raise _OfficeCoverageError("XLSX con cadena compartida inválida") from exc
    if cell_type == "inlineStr":
        inline = next((node for node in cell if _xml_local_name(node) == "is"), None)
        return _xml_text(inline) if inline is not None else ""
    if value:
        return value
    formula = next((node for node in cell if _xml_local_name(node) == "f"), None)
    return _xml_text(formula) if formula is not None else ""


def _word_table_rows(table: ElementTree.Element) -> list[str]:
    rows: list[str] = []
    for row in table.iter():
        if _xml_local_name(row) != "tr":
            continue
        cells = [_xml_text(cell) for cell in list(row) if _xml_local_name(cell) == "tc"]
        material = " | ".join(value for value in cells if value)
        if material:
            rows.append(material)
    return rows


def _drawing_table_rows(table: ElementTree.Element) -> list[str]:
    rows: list[str] = []
    for row in table.iter():
        if _xml_local_name(row) != "tr":
            continue
        cells = [_xml_text(cell) for cell in list(row) if _xml_local_name(cell) == "tc"]
        material = " | ".join(value for value in cells if value)
        if material:
            rows.append(material)
    return rows


def _odf_table_rows(table: ElementTree.Element) -> list[str]:
    rows: list[str] = []
    for row in table.iter():
        if _xml_local_name(row) != "table-row":
            continue
        cells = [child for child in list(row) if _xml_local_name(child) == "table-cell"]
        values = [_odf_cell_text(cell) for cell in cells]
        if _repeat_count(row, "number-rows-repeated") > 1 and any(values):
            raise _OfficeCoverageError("fila ODF repetida que AnyDoc no permite verificar")
        for cell, value in zip(cells, values, strict=True):
            if _repeat_count(cell, "number-columns-repeated") > 1 and value:
                raise _OfficeCoverageError("celda ODF repetida que AnyDoc no permite verificar")
        material = " | ".join(value for value in values if value)
        if material:
            rows.append(material)
    return rows


def _odf_cell_text(cell: ElementTree.Element) -> str:
    return (
        _xml_text(cell)
        or _xml_attribute(cell, "value")
        or _xml_attribute(cell, "date-value")
        or ""
    )


def _repeat_count(element: ElementTree.Element, name: str) -> int:
    value = _xml_attribute(element, name)
    if not value:
        return 1
    try:
        return int(value)
    except ValueError as exc:
        raise _OfficeCoverageError("atributo de repetición ODF inválido") from exc


def _xml_textual_blocks(root: ElementTree.Element, local_names: set[str]) -> list[str]:
    return [
        text
        for node in root.iter()
        if _xml_local_name(node) in local_names and (text := _xml_text(node))
    ]


def _xml_text(node: ElementTree.Element) -> str:
    return " ".join("".join(node.itertext()).split())


def _xml_local_name(node: ElementTree.Element) -> str:
    return node.tag.rsplit("}", maxsplit=1)[-1]


def _xml_attribute(node: ElementTree.Element, local_name: str) -> str | None:
    return next(
        (
            value
            for name, value in node.attrib.items()
            if name.rsplit("}", maxsplit=1)[-1] == local_name
        ),
        None,
    )


def _relationship_id(node: ElementTree.Element) -> str | None:
    return next(
        (
            value
            for name, value in node.attrib.items()
            if name.endswith("}id") and "relationships" in name
        ),
        None,
    )


def _normalise_coverage_text(value: str) -> str:
    normalised = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^\w]+", " ", normalised).strip()


def _coverage_anchors(values: Iterable[str]) -> list[str]:
    anchors: list[str] = []
    for value in values:
        normalised = _normalise_coverage_text(value)
        if not normalised:
            continue
        # Secuencia contigua de ventanas: perder o reordenar el centro de un
        # bloque largo impide declararlo íntegro.
        anchors.extend(
            normalised[start : start + 160]
            for start in range(0, len(normalised), 160)
        )
    return anchors


def _markdown_table_count(markdown: str) -> int:
    separator = re.compile(
        r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$"
    )
    separators = [
        line for line in markdown.splitlines() if "|" in line and separator.fullmatch(line)
    ]
    html_tables = re.findall(r"(?i)<table\b", markdown)
    return len(separators) + len(html_tables)


def _require_command(name: str) -> str:
    resolved = shutil.which(name)
    if not resolved:
        raise ExtractionError(f"no se encontró el binario local {name}")
    return resolved


def _run(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    if not command:
        raise ExtractionError("la herramienta documental no recibió comando")
    if shutil.disk_usage(tempfile.gettempdir()).free < MAX_TOOL_OUTPUT_BYTES:
        raise ExtractionError("espacio temporal insuficiente para la herramienta documental")
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            command,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if _stream_size(stdout_file) + _stream_size(stderr_file) > MAX_TOOL_OUTPUT_BYTES:
                _kill_process_group(process)
                process.wait()
                raise ExtractionError("la herramienta documental excedió el límite de salida")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _kill_process_group(process)
                process.wait()
                raise ExtractionError(
                    f"tiempo agotado al ejecutar {Path(command[0]).name}"
                )
            time.sleep(min(0.05, remaining))
        if _stream_size(stdout_file) + _stream_size(stderr_file) > MAX_TOOL_OUTPUT_BYTES:
            raise ExtractionError("la herramienta documental excedió el límite de salida")
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read().decode("utf-8", errors="replace")
        stderr = stderr_file.read().decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _stream_size(handle: Any) -> int:
    return int(os.fstat(handle.fileno()).st_size)


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        if hasattr(os, "killpg"):
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - fallback para intérpretes sin POSIX.
            process.kill()
    except ProcessLookupError:
        pass


def _assert_tool_output_size(path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ExtractionError("no se pudo medir la salida de la herramienta documental") from exc
    if size > MAX_TOOL_OUTPUT_BYTES:
        raise ExtractionError("la salida de la herramienta documental excede el límite")


def _run_json(command: list[str], timeout: float) -> dict[str, Any]:
    completed = _run(command, timeout)
    if completed.returncode != 0:
        raise ExtractionError(_bounded_error(completed.stderr or "falló la herramienta documental"))
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ExtractionError("la herramienta documental devolvió JSON inválido") from exc
    if not isinstance(value, dict):
        raise ExtractionError("la herramienta documental devolvió un contrato inválido")
    return value


def _bounded_error(value: str) -> str:
    return " ".join(value.split())[:500]


def _validate_remote_url(parts: Any, *, resolve_dns: bool = False) -> None:
    try:
        validate_official_url(parts.geturl(), resolve_dns=resolve_dns)
    except OfficialUrlError as exc:
        raise ExtractionError(str(exc)) from exc


def _resolved_public_addresses(
    host: str, port: int
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    try:
        return resolve_public_addresses(host, port)
    except OfficialUrlError as exc:
        raise ExtractionError(str(exc)) from exc


def _for_request(result: ExtractionResult, request: ExtractionRequest) -> ExtractionResult:
    values = result.to_dict()
    values["document_id"] = request.document_id
    values["source_url"] = request.url
    if result.cache_hit:
        values["duration_seconds"] = 0.0
    return ExtractionResult(**values)
