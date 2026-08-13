from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

from app.edition import (
    STATIC_ASSET_VERSION,
    item_key,
    prepare_payload,
    write_site_artifacts,
)
from app.editorial import apply_editorial
from app.validation import validate_site_artifacts
from tests.test_contract_v8 import extractive_payload


def _rewrite_bytes_and_rehash(docs: Path, relative: str, content: bytes) -> None:
    path = docs / relative
    path.write_bytes(content)
    manifest_path = docs / "data/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"][relative] = {
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def _rewrite_and_rehash(docs: Path, relative: str, payload: dict) -> None:
    content = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    _rewrite_bytes_and_rehash(docs, relative, content)


def test_transaction_writes_cross_checked_permanent_artifacts(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data" / "publications.json"

    manifest = write_site_artifacts(extractive_payload(), publications)

    written = json.loads(publications.read_text(encoding="utf-8"))
    item = written["items"][0]
    key = item_key(item["id"])
    edition = json.loads((docs / "data" / "edition.json").read_text(encoding="utf-8"))
    archive = json.loads(
        (docs / "data" / "archive" / "2026-08.json").read_text(encoding="utf-8")
    )
    item_payload = json.loads(
        (docs / "data" / "items" / f"{key}.json").read_text(encoding="utf-8")
    )

    assert written["cut_id"] == edition["cut_id"] == archive["cut_id"]
    assert written["cut_id"] == item_payload["cut_id"] == manifest["cut_id"]
    assert archive["items"][0]["id"] == item["id"]
    assert (docs / "notas" / f"{key}.html").exists()
    note = (docs / "notas" / f"{key}.html").read_text(encoding="utf-8")
    assert (
        f'<link rel="canonical" href="https://bpop06.github.io/radar-regulatorio-mx/'
        f'notas/{key}.html">' in note
    )
    assert 'class="editorial-badge is-needs-review"' in note
    assert "og:site_name" in note
    assert f'../styles.css?v={STATIC_ASSET_VERSION}' in note
    json_ld_match = re.search(
        r'<script type="application/ld\+json">(.*?)</script>', note, flags=re.DOTALL
    )
    assert json_ld_match is not None
    article = json.loads(json_ld_match.group(1))
    assert article["@context"] == "https://schema.org"
    assert article["@type"] == "Article"
    assert article["headline"] == item["official_title"]
    assert article["url"].endswith(f"notas/{key}.html")
    assert article["mainEntityOfPage"]["@id"] == article["url"]
    assert article["datePublished"] == item["official_published_at"]
    assert article["isBasedOn"] == item["canonical_url"]
    assert validate_site_artifacts(docs / "data" / "manifest.json").ok


def test_static_note_bounds_metadata_without_truncating_official_item(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    payload = extractive_payload()
    payload["items"][0]["description"] = "Documento oficial " + ("muy extenso " * 4000)

    write_site_artifacts(payload, publications)

    written = json.loads(publications.read_text(encoding="utf-8"))
    item = written["items"][0]
    note = (docs / item["detail_url"]).read_text(encoding="utf-8")
    match = re.search(r'<meta name="description" content="([^"]*)">', note)
    assert match is not None
    assert len(match.group(1)) <= 280
    assert len(item["description"]) > 10_000


def test_static_note_json_ld_cannot_be_closed_by_official_text(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    payload = extractive_payload()
    payload["items"][0]["official_title"] = "Acuerdo </script><script>alert(1)</script>"

    write_site_artifacts(payload, publications)

    item = json.loads(publications.read_text(encoding="utf-8"))["items"][0]
    note = (docs / item["detail_url"]).read_text(encoding="utf-8")
    assert note.count('<script type="application/ld+json">') == 1
    assert "</script><script>alert(1)</script>" not in note
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', note, re.DOTALL)
    assert match is not None
    assert json.loads(match.group(1))["headline"] == item["official_title"]


def test_manifest_rejects_static_note_without_article_json_ld_after_rehash(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    write_site_artifacts(extractive_payload(), publications)
    item = json.loads(publications.read_text(encoding="utf-8"))["items"][0]
    note = (docs / item["detail_url"]).read_text(encoding="utf-8")
    note = re.sub(
        r'\s*<script type="application/ld\+json">.*?</script>',
        "",
        note,
        flags=re.DOTALL,
    )
    _rewrite_bytes_and_rehash(docs, item["detail_url"], note.encode())

    report = validate_site_artifacts(docs / "data/manifest.json")

    assert not report.ok
    assert any("must declare Article JSON-LD" in error for error in report.errors)


def test_manifest_detects_artifact_tampering(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data" / "publications.json"
    write_site_artifacts(extractive_payload(), publications)
    publications.write_text("{}\n", encoding="utf-8")

    report = validate_site_artifacts(docs / "data" / "manifest.json")

    assert not report.ok
    assert any("hash does not match" in error for error in report.errors)


def test_manifest_rejects_semantically_mismatched_archive_even_with_valid_hash(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    write_site_artifacts(extractive_payload(), publications)
    relative = "data/archive/2026-08.json"
    archive = json.loads((docs / relative).read_text())
    archive["items"][0]["official_title"] = "Otro acto oficial"
    _rewrite_and_rehash(docs, relative, archive)

    report = validate_site_artifacts(docs / "data/manifest.json")

    assert not report.ok
    assert any("does not match its permanent item" in error for error in report.errors)


def test_invalid_cut_does_not_touch_previous_artifacts_or_state(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data" / "publications.json"
    valid = extractive_payload()
    valid["_pending_state"] = {"icsid": {"ARB/26/1": "Pending"}}
    write_site_artifacts(valid, publications)
    before = {
        path.relative_to(docs): path.read_bytes()
        for path in docs.rglob("*")
        if path.is_file()
    }

    invalid = extractive_payload()
    invalid["sources"][0]["status"] = "error"
    invalid["sources"][0]["error"] = "upstream unavailable"
    invalid["_pending_state"] = {"icsid": {"ARB/26/2": "Pending"}}
    with pytest.raises(Exception, match="all sources"):
        write_site_artifacts(invalid, publications)

    after = {
        path.relative_to(docs): path.read_bytes()
        for path in docs.rglob("*")
        if path.is_file()
    }
    assert after == before
    state = json.loads((docs / "data/state/icsid.json").read_text())
    assert state["schema_version"] == 8
    assert state["cut_id"] == json.loads(
        (docs / "data/manifest.json").read_text()
    )["cut_id"]
    assert state["source"] == "icsid"
    assert state["state"] == {"ARB/26/1": "Pending"}


def test_second_run_is_idempotent_except_for_cut_observation_times(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data" / "publications.json"
    payload = extractive_payload()
    first_manifest = write_site_artifacts(payload, publications)
    first = json.loads(publications.read_text(encoding="utf-8"))

    second_manifest = write_site_artifacts(deepcopy(payload), publications)
    second = json.loads(publications.read_text(encoding="utf-8"))

    assert second_manifest["cut_id"] == first_manifest["cut_id"]
    assert second == first


def test_manifest_hashes_exact_bytes(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data" / "publications.json"
    manifest = write_site_artifacts(extractive_payload(), publications)

    for relative, metadata in manifest["artifacts"].items():
        content = (docs / relative).read_bytes()
        assert metadata["sha256"] == hashlib.sha256(content).hexdigest()
        assert metadata["bytes"] == len(content)


def test_state_change_changes_cut_id_and_remains_cross_checked(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    first = extractive_payload()
    first["_pending_state"] = {"icsid": {"ARB/26/1": "Pending"}}
    first_manifest = write_site_artifacts(first, publications)

    second = extractive_payload()
    second["_pending_state"] = {"icsid": {"ARB/26/1": "Concluded"}}
    second_manifest = write_site_artifacts(second, publications)
    state = json.loads((docs / "data/state/icsid.json").read_text())

    assert second_manifest["cut_id"] != first_manifest["cut_id"]
    assert state["cut_id"] == second_manifest["cut_id"]
    assert state["state"]["ARB/26/1"] == "Concluded"
    assert validate_site_artifacts(docs / "data/manifest.json").ok


def test_manifest_recomputes_cut_id_after_coherent_state_tamper(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    payload = extractive_payload()
    payload["_pending_state"] = {"icsid": {"ARB/26/1": "Pending"}}
    write_site_artifacts(payload, publications)
    relative = "data/state/icsid.json"
    state = json.loads((docs / relative).read_text(encoding="utf-8"))
    state["state"]["ARB/26/1"] = "Concluded"
    _rewrite_and_rehash(docs, relative, state)

    report = validate_site_artifacts(docs / "data/manifest.json")

    assert not report.ok
    assert "manifest.cut_id does not match transactional artifact content" in report.errors


def test_obsolete_month_index_is_retired_when_official_month_is_corrected(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    write_site_artifacts(extractive_payload(), publications)
    old_archive = docs / "data/archive/2026-08.json"
    assert old_archive.exists()

    corrected = extractive_payload()
    corrected["generated_at"] = "2026-09-02T16:30:00+00:00"
    corrected["items"][0]["published_at"] = "2026-09-01"
    write_site_artifacts(corrected, publications)

    assert not old_archive.exists()
    assert (docs / "data/archive/2026-09.json").exists()
    assert validate_site_artifacts(docs / "data/manifest.json").ok


def test_prepare_dry_run_has_no_filesystem_side_effects(tmp_path: Path) -> None:
    result = prepare_payload(extractive_payload(), force=True)
    assert result["schema_version"] == 8
    assert list(tmp_path.iterdir()) == []


def test_item_outside_mobile_window_keeps_complete_note_and_evidence(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    initial = extractive_payload()
    initial["items"][0]["official_evidence"] = {
        "primary_url": "https://dof.gob.mx/nota?id=abc",
        "annex_url": "https://dof.gob.mx/nota_detalle_popup.php?id=abc",
    }
    write_site_artifacts(initial, publications)
    first = json.loads(publications.read_text(encoding="utf-8"))["items"][0]
    edits = tmp_path / "edits.json"
    edits.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": first["id"],
                        "content_hash": first["content_hash"],
                        "title": "SHCP modifica obligaciones y plazos del acuerdo",
                        "summary_teaser": " ".join(f"avance{i}" for i in range(45)),
                        "summary": " ".join(f"resumen{i}" for i in range(320)),
                        "card_body": (
                            "## Qué se publicó\n\nAcuerdo de SHCP.\n\n"
                            "## Sustancia\n\nCambio jurídico concreto.\n\n"
                            "## Fuente\n\n[Abrir fuente](https://dof.gob.mx/nota?id=abc)"
                        ),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    apply_editorial(edits, publications)
    completed = json.loads(publications.read_text(encoding="utf-8"))["items"][0]
    detail_path = docs / completed["detail_url"]

    later = extractive_payload()
    later["generated_at"] = "2026-10-01T16:30:00+00:00"
    later["items"] = []
    later["total_items"] = 0
    later["sources"][0]["items_found"] = 0
    write_site_artifacts(later, publications)

    permanent = json.loads(
        (docs / "data/items" / f"{item_key(completed['id'])}.json").read_text()
    )["item"]
    archive = json.loads((docs / "data/archive/2026-08.json").read_text())
    assert permanent["editorial_status"] == "complete"
    assert permanent["summary"] == completed["summary"]
    assert permanent["official_evidence"] == completed["official_evidence"]
    assert archive["items"][0]["detail_url"] == completed["detail_url"]
    assert detail_path.exists()
    assert "SHCP modifica" in detail_path.read_text(encoding="utf-8")
