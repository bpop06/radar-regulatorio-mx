from __future__ import annotations

import hashlib
import threading
import time
import zipfile
from pathlib import Path

import pytest

from app import extraction
from app.extraction import DocumentExtractor, ExtractionRequest, ExtractionResult
from app.storage import Storage


def test_extract_many_caps_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    active = 0
    maximum = 0
    lock = threading.Lock()
    extractor = DocumentExtractor()

    def fake(request: ExtractionRequest) -> ExtractionResult:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.01)
        seen.append(request.document_id)
        with lock:
            active -= 1
        return ExtractionResult(request.document_id, request.url, "complete")

    monkeypatch.setattr(extractor, "_extract_safely", fake)
    requests = [ExtractionRequest(str(index), f"https://example.com/{index}") for index in range(5)]

    results = extractor.extract_many(requests, max_workers=99)

    assert len(results) == 5
    assert sorted(seen) == ["0", "1", "2", "3", "4"]
    assert maximum <= 3


def test_extract_many_downloads_same_url_once_despite_metadata_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake(request):
        nonlocal calls
        calls += 1
        return ExtractionResult(
            request.document_id,
            request.url,
            "complete",
            markdown="Documento compartido",
            content_hash="a" * 64,
        )

    extractor = DocumentExtractor()
    monkeypatch.setattr(extractor, "_extract_safely", fake)
    results = extractor.extract_many([
        ExtractionRequest("a", "https://example.com/documento", "1" * 64),
        ExtractionRequest("b", "https://example.com/documento", "2" * 64),
    ])
    assert calls == 1
    assert [result.document_id for result in results] == ["a", "b"]


def test_html_adapter_preserves_dof_table(tmp_path: Path) -> None:
    source = tmp_path / "source.html"
    source.write_text(
        "<html><body><div id='DivDetalleNota'><h1>Resolución</h1>"
        "<p>Texto oficial completo para comprobar la extracción de inicio y final.</p>"
        "<table><tr><th>RFC</th><th>Efecto</th></tr><tr><td>ABC</td><td>30 días</td></tr></table>"
        "<p>Fin del documento oficial con contenido suficiente.</p></div></body></html>",
        encoding="utf-8",
    )
    extractor = DocumentExtractor()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    result = extractor._extract_html(  # noqa: SLF001 - parser fixture
        ExtractionRequest("dof:1", "https://dof.gob.mx/nota"),
        source,
        "text/html",
        digest,
    )

    assert result.status == "complete"
    assert "# Resolución" in result.markdown
    assert "| RFC | Efecto |" in result.markdown
    assert result.diagnostics["tables"] == 1


def test_dof_adapter_downloads_and_hashes_linked_annex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.html"
    source.write_text(
        "<div id='DivDetalleNota'><h1>Resolución</h1>"
        "<p>Cuerpo oficial con contenido suficiente para validar el inicio, "
        "la sustancia normativa y el final del documento publicado.</p>"
        "<a href='/anexo.html'>Anexo técnico</a></div>",
        encoding="utf-8",
    )
    extractor = DocumentExtractor()

    def download(url: str, directory: Path) -> tuple[Path, str, str]:
        annex = directory / "source.html"
        annex.write_text(
            "<main><h2>Anexo técnico</h2><p>Contenido íntegro del anexo oficial "
            "con inicio, tabla y final verificables.</p></main>",
            encoding="utf-8",
        )
        return annex, "text/html", "b" * 64

    monkeypatch.setattr(extractor, "_download", download)
    result = extractor._extract_dof_html(  # noqa: SLF001
        ExtractionRequest("dof:1", "https://dof.gob.mx/nota"),
        source,
        "text/html",
        "a" * 64,
    )
    assert result.status == "complete"
    assert "# Anexo 1" in result.markdown
    assert "Contenido íntegro del anexo" in result.markdown
    assert result.content_hash not in {"a" * 64, "b" * 64}
    assert result.diagnostics["annexes"][0]["status"] == "complete"


def test_dof_adapter_rejects_external_annex_without_downloading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.html"
    source.write_text(
        "<div id='DivDetalleNota'><p>Cuerpo oficial suficientemente extenso para "
        "validar el documento sin aceptar instrucciones externas maliciosas.</p>"
        "<a href='https://evil.example/anexo.pdf'>Anexo</a></div>",
        encoding="utf-8",
    )
    extractor = DocumentExtractor()
    monkeypatch.setattr(
        extractor,
        "_download",
        lambda *_: pytest.fail("no debe descargar un anexo externo"),
    )
    result = extractor._extract_dof_html(  # noqa: SLF001
        ExtractionRequest("dof:1", "https://dof.gob.mx/nota"),
        source,
        "text/html",
        "a" * 64,
    )
    assert result.status == "partial"
    assert result.diagnostics["rejected_annex_links"] == [
        "https://evil.example/anexo.pdf"
    ]


def test_dof_adapter_discovers_official_non_pdf_annex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.html"
    source.write_text(
        "<div id='DivDetalleNota'><p>Cuerpo oficial suficientemente extenso para "
        "validar la publicación y su anexo estructurado.</p>"
        "<a href='https://dof.gob.mx/archivo/adjunto.docx'>Documento</a></div>",
        encoding="utf-8",
    )
    extractor = DocumentExtractor()
    seen: list[str] = []

    def download(url: str, directory: Path):
        seen.append(url)
        path = directory / "source.docx"
        path.write_bytes(b"fixture")
        media_type = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        return path, media_type, "b" * 64

    monkeypatch.setattr(extractor, "_download", download)
    monkeypatch.setattr(
        extractor,
        "_extract_annex",
        lambda request, *_: ExtractionResult(
            request.document_id,
            request.url,
            "complete",
            markdown="Anexo oficial completo",
            content_hash="b" * 64,
            extraction_method="anydoc",
        ),
    )
    result = extractor._extract_dof_html(  # noqa: SLF001
        ExtractionRequest("dof:1", "https://dof.gob.mx/nota"),
        source,
        "text/html",
        "a" * 64,
    )
    assert result.status == "complete"
    assert seen == ["https://dof.gob.mx/archivo/adjunto.docx"]


def test_dof_adapter_uses_embed_source_attribute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.html"
    source.write_text(
        "<div id='DivDetalleNota'><p>Cuerpo oficial suficientemente extenso para "
        "validar la publicación y su documento embebido.</p>"
        "<iframe src='https://dof.gob.mx/anexo.pdf'></iframe></div>",
        encoding="utf-8",
    )
    extractor = DocumentExtractor()
    seen: list[str] = []

    def download(url: str, directory: Path):
        seen.append(url)
        path = directory / "source.pdf"
        path.write_bytes(b"fixture")
        return path, "application/pdf", "b" * 64

    monkeypatch.setattr(extractor, "_download", download)
    monkeypatch.setattr(
        extractor,
        "_extract_annex",
        lambda request, *_: ExtractionResult(
            request.document_id,
            request.url,
            "complete",
            markdown="Anexo embebido completo",
            content_hash="b" * 64,
            extraction_method="pdf-inspector",
        ),
    )
    result = extractor._extract_dof_html(  # noqa: SLF001
        ExtractionRequest("dof:1", "https://dof.gob.mx/nota"),
        source,
        "text/html",
        "a" * 64,
    )
    assert result.status == "complete"
    assert seen == ["https://dof.gob.mx/anexo.pdf"]


def test_dof_adapter_treats_unlabelled_embed_as_material_annex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.html"
    source.write_text(
        "<div id='DivDetalleNota'><p>Cuerpo oficial suficientemente extenso para "
        "validar la publicación, comprobar su continuidad y conservar todos sus "
        "elementos materiales.</p><iframe src='/visor?doc=42'></iframe></div>",
        encoding="utf-8",
    )
    extractor = DocumentExtractor()
    seen: list[str] = []

    def download(url: str, directory: Path):
        seen.append(url)
        raise extraction.ExtractionError("formato no verificable")

    monkeypatch.setattr(extractor, "_download", download)
    result = extractor._extract_dof_html(  # noqa: SLF001
        ExtractionRequest("dof:1", "https://dof.gob.mx/nota"),
        source,
        "text/html",
        "a" * 64,
    )
    assert result.status == "partial"
    assert seen == ["https://dof.gob.mx/visor?doc=42"]


def test_compound_dof_source_never_uses_main_etag_shortcut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extractor = DocumentExtractor()
    cached = ExtractionResult(
        "dof:1",
        "https://dof.gob.mx/nota",
        "complete",
        content_hash="a" * 64,
        diagnostics={
            "annex_links": ["https://dof.gob.mx/anexo.pdf"],
            "http_validators": {"etag": '"main"'},
        },
    )
    monkeypatch.setattr(
        extraction.httpx,
        "Client",
        lambda **_: pytest.fail("no debe usar HEAD para una fuente compuesta"),
    )
    assert not extractor.source_is_unchanged(
        ExtractionRequest("dof:1", "https://dof.gob.mx/nota"), cached
    )


def test_pinned_backend_connects_to_validated_ip_not_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        extraction,
        "_resolved_public_addresses",
        lambda host, port: (extraction.ipaddress.ip_address("93.184.216.34"),),
    )

    def connect(self, host, port, timeout=None, local_address=None, socket_options=None):
        seen.append(host)
        return object()

    monkeypatch.setattr(extraction.SyncBackend, "connect_tcp", connect)
    backend = extraction._PinnedDNSBackend()  # noqa: SLF001
    backend.connect_tcp("official.example", 443)
    assert seen == ["93.184.216.34"]


def test_storage_extraction_cache_and_atomic_updates(tmp_path: Path) -> None:
    database = tmp_path / "radar.sqlite3"
    payload = {
        "generated_at": "2026-08-13T12:00:00+00:00",
        "lookback_days": 31,
        "total_items": 1,
        "sources": [],
        "items": [{"id": "dof:1", "published_at": "2026-08-13"}],
    }
    result = {
        "document_id": "dof:1",
        "source_url": "https://dof.gob.mx/nota",
        "status": "complete",
        "markdown": "texto",
        "content_hash": "a" * 64,
        "media_type": "text/html",
        "extraction_method": "html-adapter",
        "diagnostics": {},
        "cache_hit": False,
        "error": None,
    }

    with Storage(database) as storage:
        storage.save_run(payload)
        storage.put_extraction(result, "v1")
        assert storage.get_extraction(result["source_url"], "v1") == result
        storage.update_document_fields_atomic(
            {"dof:1": {"extraction_status": "complete"}}
        )
        assert storage.pending_editorial_items()[0]["extraction_status"] == "complete"
        with pytest.raises(KeyError):
            storage.update_document_fields_atomic(
                {"missing": {"editorial_status": "complete"}}
            )


def test_atomic_editorial_batch_rolls_back_on_source_hash_race(tmp_path: Path) -> None:
    database = tmp_path / "radar.sqlite3"
    payload = {
        "generated_at": "2026-08-13T12:00:00+00:00",
        "lookback_days": 31,
        "total_items": 2,
        "sources": [],
        "items": [
            {
                "id": document_id,
                "published_at": "2026-08-13",
                "source_content_hash": source_hash,
                "editorial_status": "needs_review",
            }
            for document_id, source_hash in (("a", "a" * 64), ("b", "b" * 64))
        ],
    }
    with Storage(database) as storage:
        storage.save_run(payload)
        with pytest.raises(ValueError, match="la fuente cambió"):
            storage.update_document_fields_atomic(
                {
                    "a": {"editorial_status": "complete"},
                    "b": {"editorial_status": "complete"},
                },
                expected_source_hashes={"a": "a" * 64, "b": "c" * 64},
            )
        assert storage.get_document("a")["editorial_status"] == "needs_review"
        assert storage.get_document("b")["editorial_status"] == "needs_review"


def test_cache_is_invalidated_by_new_evidence_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fake_download(self, url, directory):
        nonlocal calls
        calls += 1
        path = directory / "source.html"
        path.write_text("<main><p>Texto oficial completo</p></main>", encoding="utf-8")
        return path, "text/html", str(calls) * 64

    monkeypatch.setattr(DocumentExtractor, "_download", fake_download)
    with Storage(tmp_path / "state.sqlite3") as storage:
        extractor = DocumentExtractor(cache=storage)
        first = extractor.extract(
            ExtractionRequest("x", "https://example.test/a", "a" * 64)
        )
        second = extractor.extract(
            ExtractionRequest("x", "https://example.test/a", "b" * 64)
        )
    assert first.cache_hit is False
    assert second.cache_hit is False
    assert calls == 2


def test_extractor_version_bump_never_reuses_old_complete_cache(tmp_path: Path) -> None:
    result = {
        "document_id": "x",
        "source_url": "https://example.test/a",
        "status": "complete",
        "markdown": "texto viejo",
        "content_hash": "a" * 64,
        "media_type": "text/html",
        "extraction_method": "html-adapter",
        "diagnostics": {},
        "cache_hit": False,
        "error": None,
    }
    with Storage(tmp_path / "state.sqlite3") as storage:
        storage.put_extraction(result, "document-tools-v1", "meta")
        assert storage.get_extraction(
            result["source_url"], extraction.EXTRACTOR_VERSION, "meta"
        ) is None


def test_extract_many_uses_sqlite_cache_only_on_calling_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_uncached(self, request):
        return ExtractionResult(
            request.document_id,
            request.url,
            "complete",
            markdown="texto completo",
            content_hash="c" * 64,
        )

    monkeypatch.setattr(DocumentExtractor, "_extract_uncached", fake_uncached)
    requests = [
        ExtractionRequest(str(index), f"https://example.test/{index}", str(index))
        for index in range(4)
    ]
    with Storage(tmp_path / "state.sqlite3") as storage:
        extractor = DocumentExtractor(cache=storage)
        first = extractor.extract_many(requests)
        second = extractor.extract_many(requests)
    assert len(first) == 4
    assert all(result.cache_hit for result in second)


def test_extract_many_batches_cache_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BatchCache:
        def __init__(self) -> None:
            self.batches: list[list[tuple[dict, str, str]]] = []

        def get_extraction(self, *_args):
            return None

        def put_extractions(self, values):
            self.batches.append(list(values))

    def fake_uncached(self, request):
        return ExtractionResult(
            request.document_id,
            request.url,
            "complete",
            markdown="texto completo",
            content_hash="c" * 64,
        )

    monkeypatch.setattr(DocumentExtractor, "_extract_uncached", fake_uncached)
    cache = BatchCache()
    extractor = DocumentExtractor(cache=cache)

    extractor.extract_many([
        ExtractionRequest("a", "https://example.test/a", "a"),
        ExtractionRequest("b", "https://example.test/b", "b"),
    ])

    assert len(cache.batches) == 1
    assert len(cache.batches[0]) == 2


def test_tool_timeout_kills_its_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        pid = 321
        returncode = -9

        def poll(self):
            return None

        def wait(self):
            return self.returncode

    created: dict[str, object] = {}
    killed: list[tuple[int, int]] = []

    def fake_popen(command, **kwargs):
        created["command"] = command
        created.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(extraction.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(extraction.os, "killpg", lambda pid, signal: killed.append((pid, signal)))

    with pytest.raises(extraction.ExtractionError, match="tiempo agotado"):
        extraction._run(["tool"], 0.01)  # noqa: SLF001 - process boundary contract

    assert created["start_new_session"] is True
    assert killed == [(321, extraction.signal.SIGKILL)]


def test_tool_output_is_bounded_on_disk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FinishedProcess:
        pid = 322
        returncode = 0

        def poll(self):
            return 0

    def fake_popen(_command, **kwargs):
        kwargs["stdout"].write(b"01234567890")
        kwargs["stdout"].flush()
        return FinishedProcess()

    monkeypatch.setattr(extraction, "MAX_TOOL_OUTPUT_BYTES", 10)
    monkeypatch.setattr(extraction.subprocess, "Popen", fake_popen)

    with pytest.raises(extraction.ExtractionError, match="límite de salida"):
        extraction._run(["tool"], 1)  # noqa: SLF001 - process boundary contract


def test_extractor_fails_before_reserving_unavailable_temporary_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Disk:
        free = 1

    monkeypatch.setattr(extraction.shutil, "disk_usage", lambda _path: Disk())

    with pytest.raises(extraction.ExtractionError, match="espacio temporal insuficiente"):
        DocumentExtractor()._reserve_temporary_space()  # noqa: SLF001 - budget contract


def _pdf_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    responses: list[dict],
    *,
    ocr_returncode: int = 0,
) -> ExtractionResult:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF fixture")
    directory = tmp_path / "work"
    directory.mkdir()
    values = iter(responses)
    monkeypatch.setattr(extraction, "_require_command", lambda name: name)
    monkeypatch.setattr(extraction, "_run_json", lambda *args, **kwargs: next(values))

    def fake_run(command, timeout):
        if ocr_returncode == 0:
            Path(command[-1]).write_bytes(b"%PDF ocr")
        return extraction.subprocess.CompletedProcess(
            command, ocr_returncode, "", "OCR falló" if ocr_returncode else ""
        )

    monkeypatch.setattr(extraction, "_run", fake_run)
    return DocumentExtractor()._extract_pdf(  # noqa: SLF001 - contract fixture
        ExtractionRequest("pdf:1", "https://example.com/source.pdf"),
        source,
        "application/pdf",
        "a" * 64,
        directory,
    )


def test_text_pdf_requires_matching_page_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    complete = _pdf_call(tmp_path, monkeypatch, [
        {"pdfType": "TextBased", "pagesNeedingOcr": [], "pageCount": 2},
        {"markdown": "Inicio\n\nFinal", "pagesNeedingOcr": [], "pageCount": 2},
    ])
    assert complete.status == "complete"


@pytest.mark.parametrize("pdf_type", ["Scanned", "Mixed"])
def test_scanned_and_mixed_pdf_use_local_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pdf_type: str
) -> None:
    result = _pdf_call(tmp_path, monkeypatch, [
        {"pdfType": pdf_type, "pagesNeedingOcr": [1], "pageCount": 2},
        {"pdfType": "TextBased", "pagesNeedingOcr": [], "pageCount": 2},
        {"markdown": "Inicio\n\nFinal", "pagesNeedingOcr": [], "pageCount": 2},
    ])
    assert result.status == "complete"
    assert result.extraction_method == "ocrmypdf+pdf-inspector"


def test_pdf_ocr_failure_never_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _pdf_call(
        tmp_path,
        monkeypatch,
        [{"pdfType": "Scanned", "pagesNeedingOcr": [1], "pageCount": 1}],
        ocr_returncode=1,
    )
    assert result.status == "needs_ocr"


def test_pdf_ocr_page_loss_never_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _pdf_call(tmp_path, monkeypatch, [
        {"pdfType": "Mixed", "pagesNeedingOcr": [1], "pageCount": 2},
        {"pdfType": "TextBased", "pagesNeedingOcr": [], "pageCount": 1},
        {"markdown": "Sólo una página", "pagesNeedingOcr": [], "pageCount": 1},
    ])
    assert result.status == "partial"


@pytest.mark.parametrize(
    "extracted",
    [
        {"markdown": "texto", "pagesNeedingOcr": [], "pageCount": 1},
        {"markdown": "texto", "pagesNeedingOcr": [2], "pageCount": 2},
        {"markdown": "texto", "pagesNeedingOcr": [], "pageCount": 2, "hasEncodingIssues": True},
    ],
)
def test_partial_or_invalid_pdf_diagnostics_never_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extracted: dict
) -> None:
    result = _pdf_call(tmp_path, monkeypatch, [
        {"pdfType": "TextBased", "pagesNeedingOcr": [], "pageCount": 2},
        extracted,
    ])
    assert result.status == "partial"


def test_pdf_detected_table_missing_from_markdown_never_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _pdf_call(tmp_path, monkeypatch, [
        {"pdfType": "TextBased", "pagesNeedingOcr": [], "pageCount": 1},
        {
            "markdown": "Inicio sin tabla y final",
            "pagesNeedingOcr": [],
            "pageCount": 1,
            "pagesWithTables": [1],
        },
    ])
    assert result.status == "partial"
    assert "tablas" in (result.error or "")


def test_html_table_not_preserved_never_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.html"
    source.write_text(
        "<main><p>Contenido oficial suficientemente largo para validar inicio y final. "
        "La medida establece obligaciones regulatorias verificables.</p>"
        "<table><caption>Tabla vacía</caption></table></main>",
        encoding="utf-8",
    )
    result = DocumentExtractor()._extract_generic_html(  # noqa: SLF001
        ExtractionRequest("html:1", "https://example.com/source"),
        source,
        "text/html",
        "a" * 64,
    )
    assert result.status == "partial"


def test_html_with_invalid_utf8_never_completes(tmp_path: Path) -> None:
    source = tmp_path / "source.html"
    source.write_bytes(
        b"<main><p>Contenido oficial largo y verificable " + b"\xff" + b" final.</p></main>"
    )
    with pytest.raises(extraction.ExtractionError, match="charset no verificables"):
        DocumentExtractor()._extract_generic_html(  # noqa: SLF001
            ExtractionRequest("html:1", "https://example.com/source"),
            source,
            "text/html",
            "a" * 64,
        )


def test_encrypted_pdf_and_unsupported_format_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encrypted = _pdf_call(
        tmp_path,
        monkeypatch,
        [{"pdfType": "Encrypted", "pagesNeedingOcr": [], "pageCount": 1}],
        ocr_returncode=1,
    )
    assert encrypted.status != "complete"
    assert not encrypted.markdown

    extractor = DocumentExtractor()
    source = tmp_path / "source.bin"
    source.write_bytes(b"unsupported")
    monkeypatch.setattr(
        extractor,
        "_download",
        lambda *_: (source, "application/octet-stream", "a" * 64),
    )
    unsupported = extractor._extract_uncached(  # noqa: SLF001
        ExtractionRequest("bin:1", "https://example.com/source.bin")
    )
    assert unsupported.status == "unsupported"
    assert not unsupported.markdown


def test_pdf_missing_binary_and_timeout_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF")
    extractor = DocumentExtractor()
    def fake_download(url: str, directory: Path) -> tuple[Path, str, str]:
        local_pdf = directory / "source.pdf"
        local_pdf.write_bytes(b"%PDF-1.4 fixture")
        return local_pdf, "application/pdf", "a" * 64

    monkeypatch.setattr(extractor, "_download", fake_download)
    monkeypatch.setattr(
        extraction,
        "_require_command",
        lambda name: (_ for _ in ()).throw(extraction.ExtractionError("binario ausente")),
    )
    missing = extractor._extract_safely(  # noqa: SLF001
        ExtractionRequest("pdf:1", "https://example.com/source.pdf")
    )
    assert missing.status == "failed"
    assert missing.error == "binario ausente"
    monkeypatch.setattr(
        extractor,
        "_extract_uncached",
        lambda request: (_ for _ in ()).throw(extraction.subprocess.TimeoutExpired("pdf", 1)),
    )
    timed_out = extractor._extract_safely(ExtractionRequest("pdf:2", "https://example.com/b.pdf"))  # noqa: SLF001
    assert timed_out.status == "failed"


def test_office_extraction_requires_structure_for_spreadsheets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.xlsx"
    with extraction.zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            "<workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
            "<sheets><sheet name='Datos' sheetId='1'/></sheets></workbook>",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
            "<sheetData><row r='1'><c r='A1' t='inlineStr'><is><t>Encabezado</t></is>"
            "</c></row></sheetData></worksheet>",
        )
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(extraction, "_require_command", lambda name: name)

    def unstructured(command, timeout):
        Path(command[-1]).write_text("Texto sin estructura", encoding="utf-8")
        return extraction.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(extraction, "_run", unstructured)
    result = DocumentExtractor()._extract_office(  # noqa: SLF001
        ExtractionRequest("office:1", "https://example.com/source.xlsx"),
        source,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "a" * 64,
        work,
    )
    assert result.status == "partial"


def test_zip_bomb_preflight_runs_before_anydoc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.docm"
    with extraction.zipfile.ZipFile(
        source, "w", compression=extraction.zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr("word/document.xml", b"A" * 500_000)
    monkeypatch.setattr(extraction, "MAX_OFFICE_COMPRESSION_RATIO", 2)
    monkeypatch.setattr(
        extraction,
        "_require_command",
        lambda *_: pytest.fail("AnyDoc no debe ejecutarse antes del preflight"),
    )
    with pytest.raises(extraction.ExtractionError, match="compresión"):
        DocumentExtractor()._extract_office(  # noqa: SLF001
            ExtractionRequest("office:1", "https://example.com/source.docm"),
            source,
            "application/vnd.ms-word.document.macroenabled.12",
            "a" * 64,
            tmp_path,
        )


def _office_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    members: dict[str, str],
    markdown: str,
) -> ExtractionResult:
    source = tmp_path / f"source{suffix}"
    if suffix == ".csv":
        source.write_text(members["source.csv"], encoding="utf-8")
    else:
        with zipfile.ZipFile(source, "w") as archive:
            for name, value in members.items():
                archive.writestr(name, value)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(extraction, "_require_command", lambda name: name)

    def fake_run(command: list[str], timeout: float) -> extraction.subprocess.CompletedProcess[str]:
        del timeout
        Path(command[-1]).write_text(markdown, encoding="utf-8")
        return extraction.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(extraction, "_run", fake_run)
    return DocumentExtractor()._extract_office(  # noqa: SLF001 - coverage fixture
        ExtractionRequest("office:coverage", f"https://example.com/source{suffix}"),
        source,
        "application/octet-stream",
        "a" * 64,
        work,
    )


def _docx_members() -> dict[str, str]:
    return {
        "word/document.xml": """
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body>
                <w:p><w:r><w:t>Inicio DOCX irrepetible</w:t></w:r></w:p>
                <w:tbl>
                  <w:tr><w:tc><w:p><w:r><w:t>Encabezado tabla</w:t></w:r></w:p></w:tc></w:tr>
                  <w:tr><w:tc><w:p><w:r><w:t>Fila final tabla</w:t></w:r></w:p></w:tc></w:tr>
                </w:tbl>
                <w:p><w:r><w:t>Final DOCX irrepetible</w:t></w:r></w:p>
              </w:body>
            </w:document>
        """,
    }


def _pptx_members() -> dict[str, str]:
    return {
        "ppt/presentation.xml": """
            <p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <p:sldIdLst>
                <p:sldId id="256" r:id="rId1"/><p:sldId id="257" r:id="rId2"/>
              </p:sldIdLst>
            </p:presentation>
        """,
        "ppt/_rels/presentation.xml.rels": """
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Target="slides/slide1.xml"/>
              <Relationship Id="rId2" Target="slides/slide2.xml"/>
            </Relationships>
        """,
        "ppt/slides/slide1.xml": """
            <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r>
                <a:t>Inicio diapositiva uno</a:t>
              </a:r></a:p></p:txBody></p:sp>
              <a:tbl><a:tr><a:tc><a:txBody><a:p><a:r>
                <a:t>Celda PPTX</a:t>
              </a:r></a:p></a:txBody></a:tc></a:tr></a:tbl>
              </p:spTree></p:cSld>
            </p:sld>
        """,
        "ppt/slides/slide2.xml": """
            <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r>
                <a:t>Final diapositiva dos</a:t>
              </a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
            </p:sld>
        """,
    }


def _xlsx_members() -> dict[str, str]:
    return {
        "xl/workbook.xml": """
            <workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets>
                <sheet name="Hoja Uno" sheetId="1" r:id="rId1"/>
                <sheet name="Hoja Dos" sheetId="2" r:id="rId2"/>
              </sheets>
            </workbook>
        """,
        "xl/_rels/workbook.xml.rels": """
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Target="worksheets/sheet1.xml"/>
              <Relationship Id="rId2" Target="worksheets/sheet2.xml"/>
            </Relationships>
        """,
        "xl/sharedStrings.xml": """
            <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <si><t>Inicio hoja uno</t></si><si><t>Final hoja dos</t></si>
            </sst>
        """,
        "xl/worksheets/sheet1.xml": """
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData>
            </worksheet>
        """,
        "xl/worksheets/sheet2.xml": """
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData><row r="1"><c r="A1" t="s"><v>1</v></c></row></sheetData>
            </worksheet>
        """,
    }


def _odt_members() -> dict[str, str]:
    return {
        "content.xml": """
            <office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
                xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
                xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">
              <office:body><office:text><text:p>Inicio ODT irrepetible</text:p>
                <table:table table:name="Tabla ODT"><table:table-row><table:table-cell>
                  <text:p>Celda ODT</text:p>
                </table:table-cell></table:table-row></table:table>
                <text:p>Final ODT irrepetible</text:p>
              </office:text></office:body>
            </office:document-content>
        """,
    }


def _ods_members() -> dict[str, str]:
    return {
        "content.xml": """
            <office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
                xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
                xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">
              <office:body><office:spreadsheet>
                <table:table table:name="Hoja ODS"><table:table-row><table:table-cell>
                  <text:p>Inicio y final ODS</text:p>
                </table:table-cell></table:table-row></table:table>
              </office:spreadsheet></office:body>
            </office:document-content>
        """,
    }


def _odp_members() -> dict[str, str]:
    return {
        "content.xml": """
            <office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
                xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
                xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0">
              <office:body><office:presentation>
                <draw:page draw:name="page1"><text:p>Inicio ODP uno</text:p></draw:page>
                <draw:page draw:name="page2"><text:p>Final ODP dos</text:p></draw:page>
              </office:presentation></office:body>
            </office:document-content>
        """,
    }


@pytest.mark.parametrize(
    ("suffix", "members", "markdown"),
    [
        (
            ".docx",
            _docx_members(),
            """Inicio DOCX irrepetible

| Encabezado tabla |
| --- |
| Fila final tabla |

Final DOCX irrepetible""",
        ),
        (
            ".pptx",
            _pptx_members(),
            """# Diapositiva 1

Inicio diapositiva uno

| Celda PPTX |
| --- |

# Diapositiva 2

Final diapositiva dos""",
        ),
        (
            ".xlsx",
            _xlsx_members(),
            """# Hoja Uno

| Dato |
| --- |
| Inicio hoja uno |

# Hoja Dos

| Dato |
| --- |
| Final hoja dos |""",
        ),
        (
            ".odt",
            _odt_members(),
            """Inicio ODT irrepetible

| Celda ODT |
| --- |

Final ODT irrepetible""",
        ),
        (
            ".ods",
            _ods_members(),
            """# Hoja ODS

| Dato |
| --- |
| Inicio y final ODS |""",
        ),
        (
            ".odp",
            _odp_members(),
            """# Diapositiva 1

Inicio ODP uno

# Diapositiva 2

Final ODP dos""",
        ),
        (
            ".csv",
            {"source.csv": "Encabezado CSV,Valor\nInicio CSV,Final CSV\n"},
            """| Encabezado CSV | Valor |
| --- | --- |
| Inicio CSV | Final CSV |""",
        ),
    ],
)
def test_anydoc_complete_requires_verified_office_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    members: dict[str, str],
    markdown: str,
) -> None:
    result = _office_result(tmp_path, monkeypatch, suffix, members, markdown)

    assert result.status == "complete"
    assert result.diagnostics["coverage"]["verified"] is True


@pytest.mark.parametrize(
    ("suffix", "members", "markdown"),
    [
        (
            ".docx",
            _docx_members(),
            """Inicio DOCX irrepetible

| Encabezado tabla |
| --- |
| Fila final tabla |""",
        ),
        (
            ".pptx",
            _pptx_members(),
            """# Diapositiva 1

Inicio diapositiva uno""",
        ),
        (
            ".xlsx",
            _xlsx_members(),
            """# Hoja Uno

| Dato |
| --- |
| Inicio hoja uno |""",
        ),
        (
            ".odt",
            _odt_members(),
            """Inicio ODT irrepetible

Final ODT irrepetible""",
        ),
        (
            ".ods",
            _ods_members(),
            """# Hoja ODS

Inicio y final ODS""",
        ),
        (
            ".odp",
            _odp_members(),
            """# Diapositiva 1

Inicio ODP uno""",
        ),
        (
            ".csv",
            {"source.csv": "Encabezado CSV,Valor\nInicio CSV,Final CSV\n"},
            """| Encabezado CSV | Valor |
| --- | --- |""",
        ),
    ],
)
def test_anydoc_never_completes_when_office_coverage_is_truncated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    members: dict[str, str],
    markdown: str,
) -> None:
    result = _office_result(tmp_path, monkeypatch, suffix, members, markdown)

    assert result.status == "partial"
    assert result.diagnostics["coverage"]["verified"] is False


def test_anydoc_never_completes_when_docx_table_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _office_result(
        tmp_path,
        monkeypatch,
        ".docx",
        _docx_members(),
        """Inicio DOCX irrepetible

Final DOCX irrepetible""",
    )

    assert result.status == "partial"
    assert result.diagnostics["coverage"]["tables_verified"] is False


def test_anydoc_fails_closed_for_unverifiable_legacy_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.doc"
    source.write_bytes(b"legacy binary fixture")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(extraction, "_require_command", lambda name: name)

    def fake_run(command: list[str], timeout: float) -> extraction.subprocess.CompletedProcess[str]:
        del timeout
        Path(command[-1]).write_text("Contenido aparente", encoding="utf-8")
        return extraction.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(extraction, "_run", fake_run)
    result = DocumentExtractor()._extract_office(  # noqa: SLF001 - coverage fixture
        ExtractionRequest("office:legacy", "https://example.com/source.doc"),
        source,
        "application/msword",
        "a" * 64,
        work,
    )

    assert result.status == "partial"
    assert result.diagnostics["coverage"]["verified"] is False
