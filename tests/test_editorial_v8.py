from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

import app.edition as edition_module
from app.edition import item_key, write_site_artifacts_legacy
from app.editorial import EditorialError, apply_editorial
from app.validation import validate_site_artifacts
from tests.test_contract_v8 import extractive_payload

TEASER = " ".join(f"teaser{i}" for i in range(45))
SUMMARY = " ".join(f"resumen{i}" for i in range(320))
CARD_BODY = (
    "## Qué se publicó\n\nAcuerdo de SHCP.\n\n"
    "## Sustancia\n\nLa autoridad modificó obligaciones y plazos específicos.\n\n"
    "## Fuente\n\n[Abrir publicación oficial](https://dof.gob.mx/nota?id=abc)"
)


def _write_cut(tmp_path: Path) -> tuple[Path, dict]:
    publications = tmp_path / "docs/data/publications.json"
    write_site_artifacts_legacy(extractive_payload(), publications)
    payload = json.loads(publications.read_text(encoding="utf-8"))
    return publications, payload


def _edit(item: dict, **overrides: object) -> dict:
    value: dict[str, object] = {
        "id": item["id"],
        "content_hash": item["content_hash"],
        "title": "SHCP modifica obligaciones y plazos del acuerdo",
        "summary_teaser": TEASER,
        "summary": SUMMARY,
        "card_body": CARD_BODY,
    }
    value.update(overrides)
    return value


def _move_item_outside_current_window(publications: Path) -> dict:
    later = extractive_payload()
    later["generated_at"] = "2026-10-01T16:30:00+00:00"
    later["items"] = []
    later["total_items"] = 0
    later["sources"][0]["items_found"] = 0
    write_site_artifacts_legacy(later, publications)
    assert json.loads(publications.read_text(encoding="utf-8"))["items"] == []
    manifest = json.loads(publications.with_name("manifest.json").read_text(encoding="utf-8"))
    item_path = publications.parent.parent / next(
        path for path in manifest["artifacts"] if path.startswith("data/items/")
    )
    return json.loads(item_path.read_text(encoding="utf-8"))["item"]


def _artifact_bytes(docs: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(docs): path.read_bytes()
        for path in docs.rglob("*")
        if path.is_file()
    }


def test_apply_editorial_completes_item_and_regenerates_all_artifacts(tmp_path: Path) -> None:
    publications, payload = _write_cut(tmp_path)
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps({"items": [_edit(payload["items"][0])]}), encoding="utf-8")

    assert apply_editorial(edits, publications) == 1

    completed = json.loads(publications.read_text(encoding="utf-8"))["items"][0]
    assert completed["editorial_status"] == "complete"
    assert completed["review_reason"] is None
    assert completed["title"].startswith("SHCP modifica")
    assert "summary" not in completed
    permanent = json.loads(
        (publications.parent / "items" / f"{item_key(completed['id'])}.json").read_text()
    )["item"]
    assert len(permanent["summary"].split()) == 320
    assert (tmp_path / "docs" / completed["detail_url"]).exists()
    assert json.loads((tmp_path / "docs/data/manifest.json").read_text())["cut_id"]


def test_apply_editorial_completes_manifested_item_outside_current_window(
    tmp_path: Path,
) -> None:
    publications, _ = _write_cut(tmp_path)
    historical = _move_item_outside_current_window(publications)
    before_cut = json.loads(publications.with_name("manifest.json").read_text())["cut_id"]
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps({"items": [_edit(historical)]}), encoding="utf-8")

    assert apply_editorial(edits, publications) == 1

    current = json.loads(publications.read_text(encoding="utf-8"))
    assert current["items"] == []
    assert current["total_items"] == 0
    key = item_key(historical["id"])
    permanent = json.loads(
        (publications.parent / "items" / f"{key}.json").read_text(encoding="utf-8")
    )["item"]
    archive = json.loads(
        (publications.parent / "archive" / "2026-08.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(publications.with_name("manifest.json").read_text(encoding="utf-8"))
    assert permanent["editorial_status"] == "complete"
    assert permanent["summary"] == SUMMARY
    assert archive["items"][0]["editorial_status"] == "complete"
    assert "SHCP modifica" in (
        publications.parent.parent / permanent["detail_url"]
    ).read_text(encoding="utf-8")
    assert manifest["cut_id"] != before_cut
    assert validate_site_artifacts(publications.with_name("manifest.json")).ok


def test_apply_editorial_rejects_stale_hash_for_manifested_item_without_writing(
    tmp_path: Path,
) -> None:
    publications, _ = _write_cut(tmp_path)
    historical = _move_item_outside_current_window(publications)
    edits = tmp_path / "edits.json"
    edits.write_text(
        json.dumps({"items": [_edit(historical, content_hash="0" * 64)]}),
        encoding="utf-8",
    )
    docs = publications.parent.parent
    before = _artifact_bytes(docs)

    with pytest.raises(EditorialError, match="hash de la fuente cambió"):
        apply_editorial(edits, publications)

    assert _artifact_bytes(docs) == before


def test_apply_editorial_rolls_back_manifested_item_when_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    publications, _ = _write_cut(tmp_path)
    historical = _move_item_outside_current_window(publications)
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps({"items": [_edit(historical)]}), encoding="utf-8")
    docs = publications.parent.parent
    before = _artifact_bytes(docs)
    real_replace = edition_module.os.replace
    calls = 0

    def fail_during_commit(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("fallo de commit simulado")
        real_replace(source, target)

    monkeypatch.setattr(edition_module.os, "replace", fail_during_commit)

    with pytest.raises(OSError, match="fallo de commit simulado"):
        apply_editorial(edits, publications)

    assert calls > 4
    assert _artifact_bytes(docs) == before


def test_apply_editorial_rejects_stale_content_hash_without_writing(tmp_path: Path) -> None:
    publications, payload = _write_cut(tmp_path)
    edits = tmp_path / "edits.json"
    edits.write_text(
        json.dumps({"items": [_edit(payload["items"][0], content_hash="0" * 64)]}),
        encoding="utf-8",
    )
    before = publications.read_bytes()

    with pytest.raises(EditorialError, match="hash de la fuente cambió"):
        apply_editorial(edits, publications)

    assert publications.read_bytes() == before


def test_apply_editorial_requires_reviewed_content_hash(tmp_path: Path) -> None:
    publications, payload = _write_cut(tmp_path)
    edit = _edit(payload["items"][0])
    edit.pop("content_hash")
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps({"items": [edit]}), encoding="utf-8")
    before = publications.read_bytes()

    with pytest.raises(EditorialError, match="falta el hash"):
        apply_editorial(edits, publications)

    assert publications.read_bytes() == before


def test_complete_editorial_survives_same_official_content_hash(tmp_path: Path) -> None:
    publications, payload = _write_cut(tmp_path)
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps({"items": [_edit(payload["items"][0])]}), encoding="utf-8")
    apply_editorial(edits, publications)
    complete = json.loads(publications.read_text(encoding="utf-8"))["items"][0]

    rerun = extractive_payload(content_hash=complete["content_hash"])
    write_site_artifacts_legacy(rerun, publications)
    preserved = json.loads(publications.read_text(encoding="utf-8"))["items"][0]
    permanent = json.loads(
        (publications.parent / "items" / f"{item_key(preserved['id'])}.json").read_text()
    )["item"]

    assert preserved["editorial_status"] == "complete"
    assert preserved["title"] == complete["title"]
    assert permanent["summary"] == SUMMARY
    assert permanent["first_seen_at"].startswith("2026-08-12")
    edition = json.loads((publications.parent / "edition.json").read_text())
    assert edition["signals"][0]["why_it_matters"].startswith("teaser0")


def test_editorial_importance_survives_same_content_hash(tmp_path: Path) -> None:
    publications, payload = _write_cut(tmp_path)
    edits = tmp_path / "edits.json"
    edits.write_text(
        json.dumps({"items": [_edit(payload["items"][0], importance=5)]}),
        encoding="utf-8",
    )
    apply_editorial(edits, publications)
    complete = json.loads(publications.read_text(encoding="utf-8"))["items"][0]

    rerun = extractive_payload(content_hash=complete["content_hash"])
    rerun["items"][0]["importance"] = 1
    write_site_artifacts_legacy(rerun, publications)
    preserved = json.loads(publications.read_text(encoding="utf-8"))["items"][0]

    assert preserved["importance"] == 5


def test_codex_can_apply_supported_case_analysis_through_validated_channel(
    tmp_path: Path,
) -> None:
    publications, payload = _write_cut(tmp_path)
    edits = tmp_path / "edits.json"
    edits.write_text(
        json.dumps(
            {
                "items": [
                    _edit(
                        payload["items"][0],
                        case_claim="La litis oficial versa sobre la concesión ferroviaria.",
                        case_outcome="El procedimiento permanece pendiente.",
                        case_reasoning=(
                            "La ficha oficial aún no publica razonamiento del tribunal."
                        ),
                        case_amount="No consta públicamente.",
                    )
                ]
            }
        ),
        encoding="utf-8",
    )

    apply_editorial(edits, publications)
    completed = json.loads(publications.read_text())["items"][0]
    completed = json.loads(
        (publications.parent / "items" / f"{item_key(completed['id'])}.json").read_text()
    )["item"]

    assert completed["editorial_status"] == "complete"
    assert completed["case_claim"].startswith("La litis")
    assert completed["case_outcome"].startswith("El procedimiento")
    assert completed["case_reasoning"].startswith("La ficha")
    assert completed["case_amount"] == "No consta públicamente."

    # La siguiente extracción oficial llega sin la interpretación de Codex;
    # si la evidencia base no cambió, debe conservar editorial y análisis.
    write_site_artifacts_legacy(extractive_payload(), publications)
    preserved = json.loads(publications.read_text())["items"][0]
    preserved = json.loads(
        (publications.parent / "items" / f"{item_key(preserved['id'])}.json").read_text()
    )["item"]
    assert preserved["content_hash"] == completed["content_hash"]
    assert preserved["editorial_status"] == "complete"
    assert preserved["case_claim"] == completed["case_claim"]
    assert preserved["case_outcome"] == completed["case_outcome"]
    assert preserved["case_reasoning"] == completed["case_reasoning"]
    assert preserved["case_amount"] == completed["case_amount"]


def test_changed_official_content_invalidates_complete_editorial(tmp_path: Path) -> None:
    publications, payload = _write_cut(tmp_path)
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps({"items": [_edit(payload["items"][0])]}), encoding="utf-8")
    apply_editorial(edits, publications)
    complete = json.loads(publications.read_text(encoding="utf-8"))["items"][0]

    changed = deepcopy(extractive_payload())
    changed["items"][0]["official_title"] = "La autoridad sustituyó el acuerdo por uno nuevo."
    write_site_artifacts_legacy(changed, publications)
    invalidated = json.loads(publications.read_text(encoding="utf-8"))["items"][0]

    assert invalidated["content_hash"] != complete["content_hash"]
    assert invalidated["editorial_status"] == "needs_review"
    assert invalidated["title"] is None
    assert "summary" not in invalidated
    assert "cambió" in invalidated["review_reason"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"summary_teaser": " ".join(f"x{i}" for i in range(81))},
        {"summary": " ".join(f"x{i}" for i in range(299))},
        {"summary": " ".join(f"x{i}" for i in range(1001))},
        {"card_body": "## Qué se publicó\n\nAcuerdo."},
    ],
)
def test_apply_editorial_enforces_complete_contract(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    publications, payload = _write_cut(tmp_path)
    edits = tmp_path / "edits.json"
    edits.write_text(
        json.dumps({"items": [_edit(payload["items"][0], **overrides)]}),
        encoding="utf-8",
    )
    before = publications.read_bytes()

    with pytest.raises(EditorialError):
        apply_editorial(edits, publications)
    assert publications.read_bytes() == before
