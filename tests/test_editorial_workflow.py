from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app import cli
from app.config import Settings
from app.edition import item_key, prepare_payload, write_site_artifacts
from app.editorial import EditorialError, apply_editorial
from app.extraction import EXTRACTOR_VERSION, DocumentExtractor, ExtractionResult
from app.storage import Storage
from app.validation import validate_site_artifacts
from tests.test_contract_v8 import extractive_payload


def _structured_fields() -> dict:
    return {
        "executive_summary": [
            "La autoridad modificó obligaciones y plazos con efectos verificables.",
            "La medida exige revisar operaciones y documentación relacionada.",
        ],
        "detailed_summary": [
            {
                "heading": "Regla principal",
                "paragraphs": ["El acuerdo establece una obligación y su plazo."],
                "evidence_refs": ["p1"],
            }
        ],
        "impacts": {
            "general": {
                "paragraphs": ["No se identifica un impacto general directo."],
                "evidence_refs": ["p1"],
            },
            "sectors": [
                {
                    "sector": "Contribuyentes afectados",
                    "paragraphs": ["Deben revisar sus operaciones fiscales."],
                    "evidence_refs": ["p1"],
                }
            ],
        },
        "recommended_actions": [
            {
                "affected_group": "Contribuyentes afectados",
                "paragraphs": ["Conservar evidencia y revisar la declaración aplicable."],
                "trigger": "Haber recibido el comprobante señalado",
                "deadline": "30 días naturales",
                "legal_basis": "Artículo 49 Bis del Código Fiscal",
                "evidence_refs": ["p1"],
            }
        ],
        "evidence": {
            "official_url": "https://dof.gob.mx/nota?id=abc",
            "retrieved_at": "2026-08-13T12:00:00+00:00",
            "content_hash": "b" * 64,
            "extraction_method": "html-adapter",
            "locators": [{"id": "p1", "label": "Cuerpo", "location": "párrafo 1"}],
        },
        "coverage": [
            {
                "source_ref": "p1",
                "disposition": "summarized",
                "target_sections": ["detailed_summary", "recommended_actions"],
                "reason": None,
            }
        ],
    }


def test_complete_only_export_hides_private_queue_and_writes_detail_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = prepare_payload(extractive_payload(), force=True)
    complete = payload["items"][0]
    complete.update(
        {
            "title": "SAT modifica obligaciones fiscales para contribuyentes afectados",
            "summary_teaser": " ".join(f"dato{i}" for i in range(45)),
            "summary": " ".join(f"resumen{i}" for i in range(320)),
            "card_body": (
                "## Qué se publicó\n\nAcuerdo de SHCP.\n\n"
                "## Sustancia\n\nModifica obligaciones.\n\n"
                "## Fuente\n\n[Abrir](https://dof.gob.mx/nota?id=abc)"
            ),
            "editorial_status": "complete",
            "extraction_status": "complete",
            "source_content_hash": "b" * 64,
            "source_revalidation_status": "complete",
            "extraction_method": "html-adapter",
            "extraction_retrieved_at": "2026-08-13T12:00:00+00:00",
            "source_sections": [{"id": "p1", "label": "Cuerpo", "order": 1}],
            "review_reason": None,
            "ai_generated": True,
            **_structured_fields(),
        }
    )
    pending = dict(complete)
    pending.update(
        {
            "id": "dof:pending",
            "source_id": "pending",
            "official_title": "Oficio 500-00 pendiente",
            "title": None,
            "summary_teaser": None,
            "summary": None,
            "card_body": None,
            "editorial_status": "needs_review",
            "extraction_status": "complete",
            "review_reason": "Pendiente",
            "ai_generated": False,
            "executive_summary": None,
            "detailed_summary": None,
            "impacts": None,
            "recommended_actions": None,
            "evidence": None,
            "coverage": None,
        }
    )
    payload["items"] = [complete, pending]
    payload["total_items"] = 2
    database = tmp_path / "state" / "radar.sqlite3"
    with Storage(database) as storage:
        storage.save_run(payload)
    output = tmp_path / "docs/data/publications.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "radar-regulatorio",
            "export-site",
            "--db",
            str(database),
            "--output",
            str(output),
            "--complete-only",
            "--details-dir",
            str(tmp_path / "docs/data/fichas"),
        ],
    )

    cli.main()

    published = json.loads(output.read_text(encoding="utf-8"))
    assert [item["id"] for item in published["items"]] == [complete["id"]]
    public_item = published["items"][0]
    assert "summary" not in public_item
    assert "executive_summary" not in public_item
    detail_path = tmp_path / "docs" / public_item["detail_data_url"]
    assert detail_path == tmp_path / "docs/data/fichas" / f"{item_key(complete['id'])}.json"
    detail = json.loads(detail_path.read_text(encoding="utf-8"))
    assert detail["executive_summary"] == complete["executive_summary"]
    assert detail["item"]["detail_data_url"] == public_item["detail_data_url"]
    assert detail["recommended_actions"][0]["affected_group"]


def test_private_db_apply_then_complete_only_export(tmp_path: Path) -> None:
    payload = prepare_payload(extractive_payload(), force=True)
    item = payload["items"][0]
    item.update(
        extraction_status="complete",
        extracted_text="Inicio. Contenido oficial completo. Final.",
        source_sections=[{"id": "p1", "label": "Cuerpo", "order": 1}],
        source_content_hash="b" * 64,
        extraction_method="html-adapter",
        extraction_version=EXTRACTOR_VERSION,
        extraction_retrieved_at="2026-08-13T12:00:00+00:00",
        source_revalidation_status="complete",
        editorial_status="needs_review",
    )
    database = tmp_path / "state.sqlite3"
    with Storage(database) as storage:
        storage.save_run(payload)
    edits = tmp_path / "edits.json"
    edit = {
        "id": item["id"],
        "source_content_hash": "b" * 64,
        "title": "SAT modifica obligaciones fiscales para contribuyentes afectados",
        "summary_teaser": " ".join(f"dato{i}" for i in range(45)),
        "summary": " ".join(f"resumen{i}" for i in range(320)),
        "card_body": (
            "## Qué se publicó\n\nAcuerdo fiscal.\n\n"
            "## Sustancia\n\nCambio verificable.\n\n"
            "## Fuente\n\n[Abrir](https://dof.gob.mx/nota?id=abc)"
        ),
        **_structured_fields(),
    }
    edits.write_text(json.dumps({"items": [edit]}), encoding="utf-8")
    public = tmp_path / "docs/data/publications.json"

    assert apply_editorial(edits, public, database) == 1
    assert not public.exists()
    with Storage(database) as storage:
        exported = storage.export_payload()
    write_site_artifacts(
        exported, public, complete_only=True,
        details_dir=tmp_path / "docs/data/fichas",
    )
    result = json.loads(public.read_text(encoding="utf-8"))
    assert [entry["id"] for entry in result["items"]] == [item["id"]]


def test_dof_5795726_title_must_state_cfdi_and_thirty_day_consequence(
    tmp_path: Path,
) -> None:
    payload = prepare_payload(extractive_payload(), force=True)
    item = payload["items"][0]
    item.update(
        id="dof:5795726",
        source_id="5795726",
        extraction_status="complete",
        extracted_text="El SAT declaró falsos los CFDI y concedió treinta días.",
        source_sections=[{"id": "p1", "label": "Cuerpo", "order": 1}],
        source_content_hash="b" * 64,
        extraction_method="html-adapter",
        extraction_retrieved_at="2026-08-13T12:00:00+00:00",
    )
    database = tmp_path / "state.sqlite3"
    with Storage(database) as storage:
        storage.save_run({**payload, "items": [item]})
    edit = {
        "id": item["id"], "source_content_hash": "b" * 64,
        "title": "SAT actualiza el listado fiscal de contribuyentes señalados",
        "summary_teaser": " ".join(f"dato{i}" for i in range(45)),
        "summary": " ".join(f"resumen{i}" for i in range(320)),
        "card_body": "## Qué se publicó\n\nListado fiscal.\n\n## Sustancia\n\nCambio.\n\n## Fuente\n\n[Fuente](https://dof.gob.mx/nota?id=abc)",
        **_structured_fields(),
    }
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps({"items": [edit]}), encoding="utf-8")
    with pytest.raises(EditorialError, match="CFDI falsos.*30 días.*efectos"):
        apply_editorial(edits, tmp_path / "publications.json", database)


def test_generic_headline_without_verifiable_consequence_is_rejected(
    tmp_path: Path,
) -> None:
    payload = prepare_payload(extractive_payload(), force=True)
    item = payload["items"][0]
    item.update(
        extraction_status="complete",
        extracted_text="Texto oficial completo.",
        source_sections=[{"id": "p1", "label": "Cuerpo", "order": 1}],
        source_content_hash="b" * 64,
        extraction_method="html-adapter",
        extraction_retrieved_at="2026-08-13T12:00:00+00:00",
    )
    database = tmp_path / "state.sqlite3"
    with Storage(database) as storage:
        storage.save_run({**payload, "items": [item]})
    edit = {
        "id": item["id"],
        "source_content_hash": "b" * 64,
        "title": "SAT publica nuevo acuerdo fiscal relevante",
        "summary_teaser": " ".join(f"dato{i}" for i in range(45)),
        "summary": " ".join(f"resumen{i}" for i in range(320)),
        "card_body": (
            "## Qué se publicó\n\nAcuerdo.\n\n"
            "## Sustancia\n\nCambio.\n\n## Fuente\n\nFuente."
        ),
        **_structured_fields(),
    }
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps({"items": [edit]}), encoding="utf-8")
    with pytest.raises(EditorialError, match="consecuencia, decisión o cambio"):
        apply_editorial(edits, tmp_path / "unused.json", database)


def test_private_apply_requires_source_byte_hash_and_rejects_extra_evidence(
    tmp_path: Path,
) -> None:
    payload = prepare_payload(extractive_payload(), force=True)
    item = payload["items"][0]
    item.update(
        extraction_status="complete",
        extracted_text="Texto oficial completo.",
        source_sections=[{"id": "p1", "label": "Cuerpo", "order": 1}],
        source_content_hash="b" * 64,
        extraction_method="html-adapter",
        extraction_retrieved_at="2026-08-13T12:00:00+00:00",
    )
    database = tmp_path / "state" / "radar.sqlite3"
    with Storage(database) as storage:
        storage.save_run({**payload, "items": [item]})
    edit = {
        "id": item["id"],
        "content_hash": item["content_hash"],
        "title": "SAT modifica obligaciones fiscales para contribuyentes afectados",
        "summary_teaser": " ".join(f"dato{i}" for i in range(45)),
        "summary": " ".join(f"resumen{i}" for i in range(320)),
        "card_body": (
            "## Qué se publicó\n\nAcuerdo.\n\n## Sustancia\n\nCambio.\n\n"
            "## Fuente\n\nFuente."
        ),
        **_structured_fields(),
    }
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps({"items": [edit]}), encoding="utf-8")
    with pytest.raises(EditorialError, match="falta el hash"):
        apply_editorial(edits, tmp_path / "unused.json", database)

    edit["source_content_hash"] = "b" * 64
    edit["evidence"] = {**edit["evidence"], "source_copy": "NO PUBLICAR"}
    edits.write_text(json.dumps({"items": [edit]}), encoding="utf-8")
    with pytest.raises(EditorialError, match="evidence está incompleta"):
        apply_editorial(edits, tmp_path / "unused.json", database)

    edit["evidence"].pop("source_copy")
    edit["coverage"][0]["target_sections"] = ["inventado"]
    edits.write_text(json.dumps({"items": [edit]}), encoding="utf-8")
    with pytest.raises(EditorialError, match="secciones destino válidas"):
        apply_editorial(edits, tmp_path / "unused.json", database)


def test_cut_metrics_accumulate_reported_editorial_tokens(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    with Storage(database) as storage:
        storage.save_cut_metrics({
            "generated_at": "2026-08-13T12:00:00+00:00",
            "duration_seconds": 1.0,
            "p50_seconds": 0.2,
            "p95_seconds": 0.4,
            "cache_hits": 0,
            "ocr_documents": 0,
            "failures": 0,
            "tokens": 0,
            "retained_items": 0,
            "total_items": 1,
        })
        storage.add_editorial_tokens(321)
        assert storage.latest_cut_metrics()["tokens"] == 321


def test_prepare_editorial_immediate_rerun_uses_zero_network_or_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "state.sqlite3"
    payload = prepare_payload(extractive_payload(), force=True)
    payload["items"][0].update(
        extraction_status="complete",
        source_revalidation_status="complete",
        source_content_hash="b" * 64,
        extracted_text="Texto oficial completo.",
        source_sections=[{"id": "chunk-1", "label": "Documento", "order": 1}],
    )
    with Storage(database) as storage:
        storage.save_run(payload)
        storage.save_cut_metrics({
            "generated_at": datetime.now(UTC).isoformat(),
            "last_network_collection_at": datetime.now(UTC).isoformat(),
            "collection_fingerprint": cli._collection_fingerprint(Settings(), None),  # noqa: SLF001
            "duration_seconds": 4.0,
            "p50_seconds": 1.0,
            "p95_seconds": 2.0,
            "cache_hits": 0,
            "ocr_documents": 0,
            "failures": 0,
            "tokens": 0,
            "retained_items": 0,
            "total_items": 1,
        })

    async def no_collect(*args, **kwargs):
        raise AssertionError("la segunda corrida no debe recolectar")

    seen: list = []

    def no_extract(self, requests):
        seen.extend(requests)
        return []

    monkeypatch.setattr(cli, "collect", no_collect)
    monkeypatch.setattr(DocumentExtractor, "extract_many", no_extract)
    monkeypatch.setattr(
        sys, "argv", ["radar-regulatorio", "prepare-editorial", "--db", str(database)]
    )
    cli.main()

    assert seen == []
    with Storage(database) as storage:
        metrics = storage.latest_cut_metrics()
    assert metrics["cache_rate"] == 1.0
    assert metrics["run_reused"] is True
    assert metrics["duration_seconds"] <= 1.0
    assert (
        "Cola preparada: total=1; extracción_completa=1; reutilizados=1"
        in capsys.readouterr().out
    )


def test_prepare_editorial_recollects_when_effective_window_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "state.sqlite3"
    payload = prepare_payload(extractive_payload(), force=True)
    payload["items"][0].update(
        extraction_status="complete",
        source_revalidation_status="complete",
        source_content_hash="b" * 64,
        extracted_text="Texto oficial completo.",
        source_sections=[{"id": "chunk-1", "label": "Documento", "order": 1}],
    )
    with Storage(database) as storage:
        storage.save_run(payload)
        storage.save_cut_metrics({
            "generated_at": datetime.now(UTC).isoformat(),
            "last_network_collection_at": datetime.now(UTC).isoformat(),
            "collection_fingerprint": cli._collection_fingerprint(Settings(), None),  # noqa: SLF001
            "duration_seconds": 1.0,
            "p50_seconds": 0.2,
            "p95_seconds": 0.4,
            "cache_hits": 0,
            "ocr_documents": 0,
            "failures": 0,
            "tokens": 0,
            "retained_items": 0,
            "total_items": 1,
        })

    calls: list[int | None] = []

    async def collected(_settings, days):
        calls.append(days)
        return extractive_payload()

    def extracted(_self, requests):
        return [
            ExtractionResult(
                request.document_id,
                request.url,
                "complete",
                markdown="Texto oficial completo.",
                content_hash="c" * 64,
                extraction_method="html-adapter",
            )
            for request in requests
        ]

    monkeypatch.setattr(cli, "collect", collected)
    monkeypatch.setattr(DocumentExtractor, "extract_many", extracted)
    monkeypatch.setattr(
        sys,
        "argv",
        ["radar-regulatorio", "prepare-editorial", "--db", str(database), "--days", "7"],
    )

    cli.main()

    assert calls == [7]
    with Storage(database) as storage:
        assert storage.latest_cut_metrics()["run_reused"] is False


def test_apply_editorial_rejects_untrusted_token_usage_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps({"items": [], "token_usage": -1}), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["radar-regulatorio", "apply-editorial", str(edits), "--db", str(tmp_path / "x.db")],
    )
    with pytest.raises(SystemExit):
        cli.main()
    assert not (tmp_path / "x.db").exists()


def test_prepare_editorial_same_byte_refresh_preserves_complete_editorial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "state.sqlite3"
    payload = prepare_payload(extractive_payload(), force=True)
    item = payload["items"][0]
    item.update(
        extraction_status="complete",
        source_content_hash="b" * 64,
        extracted_text="Texto completo",
        source_sections=cli._source_section_inventory("Texto completo", {}),  # noqa: SLF001
        extraction_method="html-adapter",
        extraction_version=EXTRACTOR_VERSION,
        editorial_status="complete",
        ai_generated=True,
        title="SAT modifica obligaciones fiscales para contribuyentes afectados",
        summary_teaser=" ".join(f"dato{i}" for i in range(45)),
        summary=" ".join(f"resumen{i}" for i in range(320)),
        card_body=(
            "## Qué se publicó\n\nAcuerdo.\n\n"
            "## Sustancia\n\nCambio.\n\n## Fuente\n\nFuente."
        ),
        **_structured_fields(),
    )
    with Storage(database) as storage:
        storage.save_run({**payload, "items": [item]})

    async def collected(*args, **kwargs):
        return {**payload, "items": [{**item, "extraction_status": "pending"}]}

    monkeypatch.setattr(cli, "collect", collected)
    monkeypatch.setattr(DocumentExtractor, "source_is_unchanged", lambda *args: False)
    monkeypatch.setattr(
        DocumentExtractor,
        "extract_many",
        lambda self, requests: [
            ExtractionResult(
                item["id"], item["url"], "complete", markdown="Texto completo",
                content_hash="b" * 64, extraction_method="html-adapter",
            )
        ],
    )
    monkeypatch.setattr(
        sys, "argv", ["radar-regulatorio", "prepare-editorial", "--db", str(database)]
    )
    cli.main()
    with Storage(database) as storage:
        refreshed = storage.export_payload()["items"][0]
    assert refreshed["editorial_status"] == "complete"
    assert refreshed["title"] == item["title"]
    assert refreshed["executive_summary"] == item["executive_summary"]


def test_editorial_queue_groups_aliases_by_source_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = prepare_payload(extractive_payload(), force=True)
    first = payload["items"][0]
    first.update(
        extraction_status="complete",
        editorial_status="needs_review",
        source_content_hash="b" * 64,
        extracted_text="Documento compartido",
    )
    second = {**first, "id": "DOF:alias", "source_id": "alias"}
    database = tmp_path / "state.sqlite3"
    with Storage(database) as storage:
        storage.save_run({**payload, "items": [first, second], "total_items": 2})
    output = tmp_path / "queue.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "radar-regulatorio", "editorial-queue", "--db", str(database),
            "--output", str(output),
        ],
    )
    cli.main()
    queue = json.loads(output.read_text(encoding="utf-8"))
    assert queue["total_items"] == 2
    assert queue["total_tasks"] == 1
    assert set(queue["items"][0]["document_ids"]) == {first["id"], second["id"]}


def test_database_apply_can_edit_historical_document_outside_latest_cut(
    tmp_path: Path,
) -> None:
    payload = prepare_payload(extractive_payload(), force=True)
    historical = payload["items"][0]
    historical.update(
        extraction_status="complete",
        editorial_status="needs_review",
        source_content_hash="b" * 64,
        extracted_text="Texto histórico completo",
        source_sections=[{"id": "p1", "label": "Cuerpo", "order": 1}],
        extraction_method="html-adapter",
        extraction_retrieved_at="2026-08-13T12:00:00+00:00",
    )
    current = {
        **historical,
        "id": "DOF:current",
        "source_id": "current",
        "published_at": "2026-08-13",
        "official_published_at": "2026-08-13",
        "content_hash": "c" * 64,
        "source_content_hash": "c" * 64,
        "extraction_retrieved_at": "2026-08-13T12:00:00+00:00",
        "url": "https://dof.gob.mx/nota?id=current",
        "canonical_url": "https://dof.gob.mx/nota?id=current",
    }
    database = tmp_path / "state.sqlite3"
    with Storage(database) as storage:
        storage.save_run({**payload, "items": [historical], "total_items": 1})
        storage.save_run({**payload, "items": [current], "total_items": 1})

    edits = tmp_path / "edits.json"
    edit = {
        "id": historical["id"],
        "source_content_hash": "b" * 64,
        "title": "SAT modifica obligaciones fiscales para contribuyentes afectados",
        "summary_teaser": " ".join(f"dato{i}" for i in range(45)),
        "summary": " ".join(f"resumen{i}" for i in range(320)),
        "card_body": (
            "## Qué se publicó\n\nAcuerdo.\n\n"
            "## Sustancia\n\nCambio.\n\n## Fuente\n\nFuente."
        ),
        **_structured_fields(),
    }
    edits.write_text(json.dumps({"items": [edit]}), encoding="utf-8")

    assert apply_editorial(edits, tmp_path / "unused.json", database) == 1
    with Storage(database) as storage:
        updated = storage.get_document(historical["id"])
        latest_ids = {item["id"] for item in storage.export_payload()["items"]}
    assert updated is not None
    assert updated["editorial_status"] == "complete"
    assert historical["id"] not in latest_ids


def test_complete_only_export_transports_historical_complete_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = prepare_payload(extractive_payload(), force=True)
    historical = payload["items"][0]
    historical.update(
        extraction_status="complete",
        source_revalidation_status="complete",
        editorial_status="complete",
        source_content_hash="b" * 64,
        extraction_method="html-adapter",
        extraction_retrieved_at="2026-08-13T12:00:00+00:00",
        source_sections=[{"id": "p1", "label": "Cuerpo", "order": 1}],
        title="SAT modifica obligaciones fiscales para contribuyentes afectados",
        summary_teaser=" ".join(f"dato{i}" for i in range(45)),
        summary=" ".join(f"resumen{i}" for i in range(320)),
        card_body=(
            "## Qué se publicó\n\nAcuerdo.\n\n"
            "## Sustancia\n\nCambio.\n\n## Fuente\n\nFuente."
        ),
        ai_generated=True,
        review_reason=None,
        **_structured_fields(),
    )
    current = {
        **historical,
        "id": "DOF:current-pending",
        "source_id": "current-pending",
        "url": "https://dof.gob.mx/nota?id=current-pending",
        "canonical_url": "https://dof.gob.mx/nota?id=current-pending",
        "published_at": "2026-08-13",
        "official_published_at": "2026-08-13",
        "extraction_status": "pending",
        "editorial_status": "needs_review",
        "ai_generated": False,
        "title": None,
        "summary_teaser": None,
        "summary": None,
        "card_body": None,
    }
    database = tmp_path / "state.sqlite3"
    with Storage(database) as storage:
        storage.save_run({**payload, "items": [historical]})
        storage.save_run({**payload, "items": [current]})
    output = tmp_path / "docs/data/publications.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "radar-regulatorio", "export-site", "--db", str(database),
            "--output", str(output), "--complete-only",
        ],
    )
    cli.main()
    assert json.loads(output.read_text())["items"] == []
    historical_path = output.parent / "items" / f"{item_key(historical['id'])}.json"
    assert historical_path.exists()


def test_export_site_retires_previous_complete_when_current_becomes_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = prepare_payload(extractive_payload(), force=True)
    complete = payload["items"][0]
    complete.update(
        extraction_status="complete",
        source_revalidation_status="complete",
        editorial_status="complete",
        source_content_hash="b" * 64,
        extraction_method="html-adapter",
        extraction_retrieved_at="2026-08-13T12:00:00+00:00",
        source_sections=[{"id": "p1", "label": "Cuerpo", "order": 1}],
        title="SAT modifica obligaciones fiscales para contribuyentes afectados",
        summary_teaser=" ".join(f"dato{i}" for i in range(45)),
        summary=" ".join(f"resumen{i}" for i in range(320)),
        card_body=(
            "## Qué se publicó\n\nAcuerdo.\n\n## Sustancia\n\nCambio.\n\n"
            "## Fuente\n\n[Fuente](https://dof.gob.mx/nota?id=abc)"
        ),
        ai_generated=True,
        review_reason=None,
        **_structured_fields(),
    )
    database = tmp_path / "state" / "radar.sqlite3"
    output = tmp_path / "docs/data/publications.json"
    with Storage(database) as storage:
        storage.save_run({**payload, "items": [complete]})
    monkeypatch.setattr(
        sys,
        "argv",
        ["radar", "export-site", "--db", str(database), "--output", str(output),
         "--complete-only"],
    )
    cli.main()
    key = item_key(complete["id"])
    paths = [
        output.parent / "items" / f"{key}.json",
        output.parent / "fichas" / f"{key}.json",
        output.parent.parent / "notas" / f"{key}.html",
    ]
    assert all(path.exists() for path in paths)

    pending = {**complete, "source_content_hash": "c" * 64}
    pending.update(
        extraction_status="complete",
        source_revalidation_status="complete",
        editorial_status="needs_review",
        ai_generated=False,
        title=None,
        summary_teaser=None,
        summary=None,
        card_body=None,
        executive_summary=None,
        detailed_summary=None,
        impacts=None,
        recommended_actions=None,
        evidence=None,
        coverage=None,
    )
    with Storage(database) as storage:
        storage.save_run({**payload, "items": [pending]})
    cli.main()
    assert json.loads(output.read_text())["items"] == []
    assert all(not path.exists() for path in paths)


def test_export_site_keeps_reaudited_item_after_source_hash_changes(
    tmp_path: Path,
) -> None:
    payload = prepare_payload(extractive_payload(), force=True)
    complete = payload["items"][0]
    complete.update(
        extraction_status="complete",
        source_revalidation_status="complete",
        editorial_status="complete",
        source_content_hash="b" * 64,
        extraction_method="html-adapter",
        extraction_retrieved_at="2026-08-13T12:00:00+00:00",
        source_sections=[{"id": "p1", "label": "Cuerpo", "order": 1}],
        title="SAT modifica obligaciones fiscales para contribuyentes afectados",
        summary_teaser=" ".join(f"dato{i}" for i in range(45)),
        summary=" ".join(f"resumen{i}" for i in range(320)),
        card_body=(
            "## Qué se publicó\n\nAcuerdo.\n\n## Sustancia\n\nCambio.\n\n"
            "## Fuente\n\n[Fuente](https://dof.gob.mx/nota?id=abc)"
        ),
        ai_generated=True,
        review_reason=None,
        **_structured_fields(),
    )
    output = tmp_path / "docs/data/publications.json"
    write_site_artifacts(
        {**payload, "items": [complete]},
        output,
        complete_only=True,
    )

    reaudited = deepcopy(complete)
    reaudited["source_content_hash"] = "c" * 64
    reaudited["evidence"] = {
        **reaudited["evidence"],
        "content_hash": "c" * 64,
    }
    write_site_artifacts(
        {**payload, "items": [reaudited]},
        output,
        complete_only=True,
    )

    key = item_key(complete["id"])
    permanent = json.loads((output.parent / "items" / f"{key}.json").read_text())["item"]
    assert permanent["id"] == complete["id"]
    assert permanent["evidence"]["content_hash"] == "c" * 64
    assert validate_site_artifacts(output.parent / "manifest.json").ok


def test_long_source_queue_uses_contiguous_fragments_without_full_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "# Encabezado\n\n" + ("contenido " * 4000)
    payload = prepare_payload(extractive_payload(), force=True)
    item = payload["items"][0]
    item.update(
        extraction_status="complete",
        editorial_status="needs_review",
        source_content_hash="b" * 64,
        extracted_text=text,
        source_sections=cli._source_section_inventory(text, {}),  # noqa: SLF001
    )
    database = tmp_path / "state.sqlite3"
    with Storage(database) as storage:
        storage.save_run({**payload, "items": [item]})
    output = tmp_path.parent / f"{tmp_path.name}-queue.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "radar-regulatorio",
            "editorial-queue",
            "--db",
            str(database),
            "--output",
            str(output),
        ],
    )
    cli.main()
    queued = json.loads(output.read_text(encoding="utf-8"))["items"][0]
    assert queued["extracted_text"] is None
    fragments = queued["source_fragments"]
    assert "".join(fragment["text"] for fragment in fragments) == text
    assert all(fragment["end"] - fragment["start"] <= 12_000 for fragment in fragments)
    assert [fragment["start"] for fragment in fragments[1:]] == [
        fragment["end"] for fragment in fragments[:-1]
    ]


def test_editorial_queue_rejects_repository_path_from_unrelated_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "state" / "radar.sqlite3"
    payload = prepare_payload(extractive_payload(), force=True)
    with Storage(database) as storage:
        storage.save_run(payload)
    repository_output = Path(cli.__file__).resolve().parents[1] / "leak.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["radar", "editorial-queue", "--db", str(database), "--output",
         str(repository_output)],
    )
    with pytest.raises(SystemExit, match="2"):
        cli.main()
    assert not repository_output.exists()


def test_prepare_editorial_bootstraps_current_archive_as_private_pending_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "publications.json"
    source.write_text(json.dumps(extractive_payload()), encoding="utf-8")
    database = tmp_path / "state" / "radar.sqlite3"
    seen: list = []

    def extract_many(self, requests):
        seen.extend(requests)
        return [
            ExtractionResult(
                request.document_id,
                request.url,
                "complete",
                markdown="Texto oficial completo para bootstrap.",
                content_hash="b" * 64,
                extraction_method="html-adapter",
            )
            for request in requests
        ]

    monkeypatch.setattr(DocumentExtractor, "extract_many", extract_many)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "radar", "prepare-editorial", "--bootstrap-input", str(source),
            "--db", str(database),
        ],
    )
    cli.main()
    assert len(seen) == 1 and seen[0].force_refresh is True
    with Storage(database) as storage:
        item = storage.export_payload()["items"][0]
    assert item["extraction_status"] == "complete"
    assert item["editorial_status"] == "needs_review"
    assert item["title"] is None
