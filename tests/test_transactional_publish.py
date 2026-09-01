from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

import app.edition as edition_module
from app.edition import (
    STATIC_ASSET_VERSION,
    PublishError,
    _consolidate_identity_aliases,
    item_key,
    prepare_payload,
    write_site_artifacts,
    write_site_artifacts_legacy,
)
from app.editorial import apply_editorial
from app.validation import validate_site_artifacts
from tests.test_contract_v8 import extractive_payload
from tests.test_editorial_workflow import _structured_fields


def _artifact_bytes(docs: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(docs): path.read_bytes()
        for path in docs.rglob("*")
        if path.is_file()
    }


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

    manifest = write_site_artifacts_legacy(extractive_payload(), publications)

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


def test_static_note_bounds_legacy_metadata(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    payload = extractive_payload()
    payload["items"][0]["description"] = "Documento oficial " + ("muy extenso " * 4000)

    write_site_artifacts_legacy(payload, publications)

    written = json.loads(publications.read_text(encoding="utf-8"))
    item = written["items"][0]
    note = (docs / item["detail_url"]).read_text(encoding="utf-8")
    match = re.search(r'<meta name="description" content="([^"]*)">', note)
    assert match is not None
    assert len(match.group(1)) <= 280
    permanent = json.loads(
        (docs / "data/items" / f"{item_key(item['id'])}.json").read_text()
    )["item"]
    assert len(permanent["description"]) > 10_000


def test_static_note_json_ld_cannot_be_closed_by_official_text(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    payload = extractive_payload()
    payload["items"][0]["official_title"] = "Acuerdo </script><script>alert(1)</script>"

    write_site_artifacts_legacy(payload, publications)

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
    write_site_artifacts_legacy(extractive_payload(), publications)
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
    write_site_artifacts_legacy(extractive_payload(), publications)
    publications.write_text("{}\n", encoding="utf-8")

    report = validate_site_artifacts(docs / "data" / "manifest.json")

    assert not report.ok
    assert any("hash does not match" in error for error in report.errors)


@pytest.mark.parametrize("kind", ["item", "archive", "state"])
def test_manifest_rejects_symlinked_artifacts(tmp_path: Path, kind: str) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    payload = extractive_payload()
    payload["_pending_state"] = {"icsid": {"ARB/26/1": "Pending"}}
    write_site_artifacts_legacy(payload, publications)
    item_path = docs / "data/items" / f"{item_key(payload['items'][0]['id'])}.json"
    relative = {
        "item": item_path,
        "archive": docs / "data/archive/2026-08.json",
        "state": docs / "data/state/icsid.json",
    }[kind]
    external = tmp_path / f"outside-{kind}.json"
    external.write_bytes(relative.read_bytes())
    relative.unlink()
    relative.symlink_to(external)

    report = validate_site_artifacts(docs / "data/manifest.json")

    assert not report.ok
    assert any("symbolic link" in error for error in report.errors)


def test_writer_refuses_to_read_a_symlinked_previous_item(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    payload = extractive_payload()
    write_site_artifacts_legacy(payload, publications)
    item_path = docs / "data/items" / f"{item_key(payload['items'][0]['id'])}.json"
    external = tmp_path / "outside.json"
    external.write_bytes(item_path.read_bytes())
    item_path.unlink()
    item_path.symlink_to(external)

    with pytest.raises(PublishError, match="no puede ser un enlace"):
        write_site_artifacts_legacy(payload, publications)


def test_manifest_rejects_private_retraction_rollback_material(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    payload = extractive_payload()
    payload["_pending_state"] = {
        "retractions": {
            "version": 1,
            "items": {
                "dof:1": {
                    "retracted_at": "2026-08-13T00:00:00+00:00",
                    "reason": "no público",
                    "record": {"id": "dof:1"},
                }
            },
        }
    }
    write_site_artifacts_legacy(payload, publications)
    path = "data/state/retractions.json"
    envelope = json.loads((docs / path).read_text(encoding="utf-8"))
    envelope["state"] = payload["_pending_state"]["retractions"]
    _rewrite_and_rehash(docs, path, envelope)

    report = validate_site_artifacts(docs / "data/manifest.json")

    assert not report.ok
    assert any("public retractions tombstone schema" in error for error in report.errors)


def test_manifest_rejects_semantically_mismatched_archive_even_with_valid_hash(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    write_site_artifacts_legacy(extractive_payload(), publications)
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
    write_site_artifacts_legacy(valid, publications)
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
        write_site_artifacts_legacy(invalid, publications)

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
    first_manifest = write_site_artifacts_legacy(payload, publications)
    first = json.loads(publications.read_text(encoding="utf-8"))

    second_manifest = write_site_artifacts_legacy(deepcopy(payload), publications)
    second = json.loads(publications.read_text(encoding="utf-8"))

    assert second_manifest["cut_id"] == first_manifest["cut_id"]
    assert second == first


def test_global_alias_consolidation_preserves_winner_times_and_diputados_anchors() -> None:
    shared_url = "https://www.gob.mx/impi/prensa/aviso?idiom=es"
    gaceta = "https://gaceta.diputados.gob.mx/Gaceta/66/2026/ago/20260812.html"
    permanent = {
        "gob.mx apf:copy": {
            "id": "gob.mx apf:copy",
            "source": "Gob.mx APF",
            "source_id": "copy",
            "url": shared_url,
            "canonical_url": shared_url,
            "description": "Copia agregada extensa.",
            "first_seen_at": "2026-08-01T10:00:00+00:00",
            "detected_at": "2026-08-01T10:00:00+00:00",
            "last_seen_at": "2026-08-10T10:00:00+00:00",
        },
        "impi:official": {
            "id": "impi:official",
            "source": "IMPI",
            "source_id": "official",
            "url": shared_url,
            "canonical_url": shared_url,
            "description": "Original.",
            "first_seen_at": "2026-08-03T10:00:00+00:00",
            "detected_at": "2026-08-03T10:00:00+00:00",
            "last_seen_at": "2026-08-12T10:00:00+00:00",
        },
        "diputados:one": {
            "id": "diputados:one",
            "source": "Diputados",
            "source_id": "one",
            "url": f"{gaceta}#Iniciativa1",
            "canonical_url": gaceta,
            "description": "Primera iniciativa.",
        },
        "diputados:two": {
            "id": "diputados:two",
            "source": "Diputados",
            "source_id": "two",
            "url": f"{gaceta}#Iniciativa2",
            "canonical_url": gaceta,
            "description": "Segunda iniciativa.",
        },
    }

    removed = _consolidate_identity_aliases(permanent)

    assert removed == ("gob.mx apf:copy",)
    assert set(permanent) == {"impi:official", "diputados:one", "diputados:two"}
    assert permanent["impi:official"]["first_seen_at"] == "2026-08-01T10:00:00+00:00"
    assert permanent["impi:official"]["detected_at"] == "2026-08-01T10:00:00+00:00"
    assert permanent["impi:official"]["last_seen_at"] == "2026-08-12T10:00:00+00:00"


def test_second_day_keeps_mobile_window_but_publishes_empty_edition(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    write_site_artifacts_legacy(extractive_payload(), publications)

    next_day = extractive_payload()
    next_day["generated_at"] = "2026-08-13T16:30:00+00:00"
    write_site_artifacts_legacy(next_day, publications)

    written = json.loads(publications.read_text(encoding="utf-8"))
    edition = json.loads((docs / "data/edition.json").read_text(encoding="utf-8"))
    assert written["total_items"] == 1
    assert written["edition"]["state"] == "empty"
    assert written["edition"]["total_today"] == 0
    assert written["edition"]["signals"] == []
    assert edition["state"] == "empty"
    assert edition["total_today"] == 0
    assert edition["signals"] == []


def test_bootstrap_repairs_inflated_detection_without_hiding_explicit_change(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    inflated = extractive_payload()
    inflated["items"] = []
    for item_id, official_date in (("inventory", "2026-08-10"), ("changed", "2026-04-27")):
        record = deepcopy(extractive_payload()["items"][0])
        record.update(
            {
                "id": f"DOF:{item_id}",
                "source_id": item_id,
                "url": f"https://dof.gob.mx/nota?id={item_id}",
                "canonical_url": f"https://dof.gob.mx/nota?id={item_id}",
                "published_at": official_date,
                "detected_at": "2026-08-12T16:30:00+00:00",
                "first_seen_at": "2026-08-12T16:30:00+00:00",
                "last_seen_at": "2026-08-12T16:30:00+00:00",
                "official_identifiers": {"source_id": item_id},
                "official_evidence": {
                    "primary_url": f"https://dof.gob.mx/nota?id={item_id}"
                },
            }
        )
        inflated["items"].append(record)
    inflated["total_items"] = 2
    write_site_artifacts_legacy(inflated, publications)

    baseline = deepcopy(inflated)
    baseline["generated_at"] = "2026-08-13T16:30:00+00:00"
    baseline["_baseline_inventory"] = True
    baseline["_explicit_detection_ids"] = ["DOF:changed"]
    baseline["items"][0].update(
        {
            "detected_at": "2026-08-10T12:00:00+00:00",
            "first_seen_at": "2026-08-10T12:00:00+00:00",
            "last_seen_at": "2026-08-13T16:30:00+00:00",
        }
    )
    baseline["items"][1].update(
        {
            "detected_at": "2026-08-13T16:30:00+00:00",
            "first_seen_at": "2026-08-13T16:30:00+00:00",
            "last_seen_at": "2026-08-13T16:30:00+00:00",
        }
    )
    write_site_artifacts_legacy(baseline, publications)

    written = json.loads(publications.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in written["items"]}
    assert by_id["DOF:inventory"]["detected_at"] == "2026-08-10T12:00:00+00:00"
    assert by_id["DOF:changed"]["detected_at"] == "2026-08-13T16:30:00+00:00"
    assert written["edition"]["total_today"] == 1
    assert [signal["id"] for signal in written["edition"]["signals"]] == ["DOF:changed"]
    assert "_baseline_inventory" not in written
    assert "_explicit_detection_ids" not in written


def test_writer_transactionally_consolidates_cross_source_url_aliases(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    official_url = "https://www.gob.mx/impi/prensa/aviso-oficial?idiom=es"
    first = extractive_payload()
    first_item = first["items"][0]
    first_item.update(
        {
            "id": "gob.mx apf:gob-copy",
            "source": "Gob.mx APF",
            "source_id": "gob-copy",
            "url": official_url,
            "canonical_url": official_url,
            "official_identifiers": {"source_id": "gob-copy"},
            "official_evidence": {"primary_url": official_url},
        }
    )
    write_site_artifacts_legacy(first, publications)
    old_key = item_key("gob.mx apf:gob-copy")
    assert (docs / f"data/items/{old_key}.json").exists()
    assert (docs / f"notas/{old_key}.html").exists()

    winner = extractive_payload()
    winner["generated_at"] = "2026-08-13T16:30:00+00:00"
    winner_item = winner["items"][0]
    winner_item.update(
        {
            "id": "impi:impi-copy",
            "source": "IMPI",
            "source_id": "impi-copy",
            "url": official_url,
            "canonical_url": official_url,
            "official_identifiers": {"source_id": "impi-copy"},
            "official_evidence": {"primary_url": official_url},
        }
    )
    write_site_artifacts_legacy(winner, publications)

    archive = json.loads((docs / "data/archive/2026-08.json").read_text())
    assert [item["id"] for item in archive["items"]] == ["impi:impi-copy"]
    assert not (docs / f"data/items/{old_key}.json").exists()
    assert not (docs / f"notas/{old_key}.html").exists()
    assert validate_site_artifacts(docs / "data/manifest.json").ok


def test_lower_priority_alias_cannot_replace_existing_specific_winner(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    official_url = "https://www.gob.mx/impi/prensa/aviso-oficial?idiom=es"
    specific = extractive_payload()
    specific["items"][0].update(
        {
            "id": "impi:specific",
            "source": "IMPI",
            "source_id": "specific",
            "url": official_url,
            "canonical_url": official_url,
            "official_identifiers": {"source_id": "specific"},
            "official_evidence": {"primary_url": official_url},
        }
    )
    write_site_artifacts_legacy(specific, publications)
    winner_key = item_key("impi:specific")

    aggregate_only = extractive_payload()
    aggregate_only["generated_at"] = "2026-08-13T16:30:00+00:00"
    aggregate_only["items"][0].update(
        {
            "id": "gob.mx apf:aggregate",
            "source": "Gob.mx APF",
            "source_id": "aggregate",
            "url": official_url,
            "canonical_url": official_url,
            "official_identifiers": {"source_id": "aggregate"},
            "official_evidence": {"primary_url": official_url},
        }
    )
    write_site_artifacts_legacy(aggregate_only, publications)

    written = json.loads(publications.read_text(encoding="utf-8"))
    archive = json.loads((docs / "data/archive/2026-08.json").read_text())
    assert [item["id"] for item in written["items"]] == ["impi:specific"]
    assert [item["id"] for item in archive["items"]] == ["impi:specific"]
    assert (docs / f"data/items/{winner_key}.json").exists()
    assert validate_site_artifacts(docs / "data/manifest.json").ok


def test_writer_consolidates_official_impi_article_alias_but_not_generic_same_source_ids(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    article_url = (
        "https://www.gob.mx/impi/es/articulos/"
        "impi-asegura-mercancia-apocrifa-en-aduana-de-lazaro-cardenas-430469?idiom=es"
    )
    press_url = (
        "https://www.gob.mx/impi/prensa/"
        "impi-asegura-mercancia-apocrifa-en-aduana-de-lazaro-cardenas?idiom=es"
    )
    article = extractive_payload()
    article["items"][0].update(
        {
            "id": "gob.mx apf:article",
            "source": "Gob.mx APF",
            "source_id": "article",
            "url": article_url,
            "canonical_url": article_url,
            "official_identifiers": {"source_id": "article"},
            "official_evidence": {"primary_url": article_url},
        }
    )
    write_site_artifacts_legacy(article, publications)

    press = extractive_payload()
    press["generated_at"] = "2026-08-13T16:30:00+00:00"
    press["items"][0].update(
        {
            "id": "gob.mx apf:press",
            "source": "Gob.mx APF",
            "source_id": "press",
            "url": press_url,
            "canonical_url": press_url,
            "official_identifiers": {"source_id": "press"},
            "official_evidence": {"primary_url": press_url},
        }
    )
    write_site_artifacts_legacy(press, publications)
    archive = json.loads((docs / "data/archive/2026-08.json").read_text())
    assert [item["id"] for item in archive["items"]] == ["gob.mx apf:press"]

    generic = "https://dof.gob.mx/nota"
    first_dof = extractive_payload()
    first_dof["generated_at"] = "2026-08-14T16:30:00+00:00"
    first_dof["items"][0].update(
        {
            "id": "DOF:one",
            "source_id": "one",
            "url": generic,
            "canonical_url": generic,
            "official_identifiers": {"source_id": "one"},
            "official_evidence": {"primary_url": generic},
        }
    )
    write_site_artifacts_legacy(first_dof, publications)
    second_dof = deepcopy(first_dof)
    second_dof["generated_at"] = "2026-08-15T16:30:00+00:00"
    second_dof["items"][0]["id"] = "DOF:two"
    second_dof["items"][0]["source_id"] = "two"
    second_dof["items"][0]["official_identifiers"] = {"source_id": "two"}
    write_site_artifacts_legacy(second_dof, publications)

    archive = json.loads((docs / "data/archive/2026-08.json").read_text())
    archived_ids = {item["id"] for item in archive["items"]}
    assert {"DOF:one", "DOF:two"} <= archived_ids
    assert validate_site_artifacts(docs / "data/manifest.json").ok


def test_manifest_hashes_exact_bytes(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data" / "publications.json"
    manifest = write_site_artifacts_legacy(extractive_payload(), publications)

    for relative, metadata in manifest["artifacts"].items():
        content = (docs / relative).read_bytes()
        assert metadata["sha256"] == hashlib.sha256(content).hexdigest()
        assert metadata["bytes"] == len(content)


def test_state_change_changes_cut_id_and_remains_cross_checked(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    first = extractive_payload()
    first["_pending_state"] = {"icsid": {"ARB/26/1": "Pending"}}
    first_manifest = write_site_artifacts_legacy(first, publications)

    second = extractive_payload()
    second["_pending_state"] = {"icsid": {"ARB/26/1": "Concluded"}}
    second_manifest = write_site_artifacts_legacy(second, publications)
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
    write_site_artifacts_legacy(payload, publications)
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
    write_site_artifacts_legacy(extractive_payload(), publications)
    old_archive = docs / "data/archive/2026-08.json"
    assert old_archive.exists()

    corrected = extractive_payload()
    corrected["generated_at"] = "2026-09-02T16:30:00+00:00"
    corrected["items"][0]["published_at"] = "2026-09-01"
    write_site_artifacts_legacy(corrected, publications)

    assert not old_archive.exists()
    assert (docs / "data/archive/2026-09.json").exists()
    assert validate_site_artifacts(docs / "data/manifest.json").ok


def test_invalidated_complete_item_retires_item_detail_and_note_transactionally(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    payload = prepare_payload(extractive_payload(), force=True)
    item = payload["items"][0]
    item.update(
        title="SAT modifica obligaciones fiscales para contribuyentes afectados",
        summary_teaser=" ".join(f"dato{i}" for i in range(45)),
        summary=" ".join(f"resumen{i}" for i in range(320)),
        card_body=(
            "## Qué se publicó\n\nAcuerdo.\n\n"
            "## Sustancia\n\nCambio.\n\n## Fuente\n\nFuente."
        ),
        extraction_status="complete",
        editorial_status="complete",
        source_content_hash="b" * 64,
        source_revalidation_status="complete",
        ai_generated=True,
        review_reason=None,
        **_structured_fields(),
    )
    write_site_artifacts(payload, publications, complete_only=True)
    key = item_key(item["id"])
    paths = (
        docs / f"data/items/{key}.json",
        docs / f"data/fichas/{key}.json",
        docs / f"notas/{key}.html",
    )
    assert all(path.exists() for path in paths)

    pending = deepcopy(payload)
    pending_item = pending["items"][0]
    pending_item.update(
        extraction_status="complete",
        editorial_status="needs_review",
        source_revalidation_status="complete",
        ai_generated=False,
        title=None,
        summary_teaser=None,
        summary=None,
        card_body=None,
        review_reason="La fuente cambió.",
    )
    write_site_artifacts(pending, publications, complete_only=True)
    assert all(not path.exists() for path in paths)
    manifest = json.loads((docs / "data/manifest.json").read_text())
    assert all(path.relative_to(docs).as_posix() not in manifest["artifacts"] for path in paths)


def test_reconcile_rejects_stale_complete_editorial_after_source_changes() -> None:
    previous = prepare_payload(extractive_payload(), force=True)["items"][0]
    previous.update(
        source_content_hash="b" * 64,
        editorial_status="complete",
        ai_generated=True,
    )
    stale = deepcopy(previous)
    stale.update(
        source_content_hash="c" * 64,
        source_revalidation_status="complete",
        evidence={"content_hash": "b" * 64},
    )

    reconciled = edition_module._reconcile_item(  # noqa: SLF001
        stale,
        previous,
        "2026-08-14T12:00:00+00:00",
    )

    assert reconciled["editorial_status"] == "needs_review"
    assert reconciled["ai_generated"] is False
    assert reconciled["title"] is None


def test_complete_only_writer_rejects_legacy_complete_without_source_hash(
    tmp_path: Path,
) -> None:
    payload = prepare_payload(extractive_payload(), force=True)
    item = payload["items"][0]
    item.update(
        extraction_status="complete",
        editorial_status="complete",
        ai_generated=True,
        title="SAT modifica obligaciones fiscales para contribuyentes afectados",
        summary_teaser=" ".join(f"dato{i}" for i in range(45)),
        summary=" ".join(f"resumen{i}" for i in range(320)),
        card_body="## Qué se publicó\n\nAcuerdo.\n\n## Sustancia\n\nCambio.",
    )
    publications = tmp_path / "docs/data/publications.json"
    write_site_artifacts(payload, publications, complete_only=True)
    assert json.loads(publications.read_text())["items"] == []


def test_invalidated_artifact_retirement_rolls_back_if_manifest_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    payload = prepare_payload(extractive_payload(), force=True)
    item = payload["items"][0]
    item.update(
        title="SAT modifica obligaciones fiscales para contribuyentes afectados",
        summary_teaser=" ".join(f"dato{i}" for i in range(45)),
        summary=" ".join(f"resumen{i}" for i in range(320)),
        card_body=(
            "## Qué se publicó\n\nAcuerdo.\n\n"
            "## Sustancia\n\nCambio.\n\n## Fuente\n\nFuente."
        ),
        extraction_status="complete",
        editorial_status="complete",
        source_content_hash="b" * 64,
        source_revalidation_status="complete",
        ai_generated=True,
        review_reason=None,
        **_structured_fields(),
    )
    write_site_artifacts(payload, publications, complete_only=True)
    before = _artifact_bytes(docs)
    pending = deepcopy(payload)
    pending["items"][0].update(
        editorial_status="needs_review",
        ai_generated=False,
        title=None,
        summary_teaser=None,
        summary=None,
        card_body=None,
        review_reason="La fuente cambió.",
    )
    real_replace = edition_module.os.replace
    failed = False

    def fail_manifest(source: Path, target: Path) -> None:
        nonlocal failed
        if (
            not failed
            and target == docs / "data/manifest.json"
            and ".radar-stage-" in str(source)
            and ".backup" not in str(source)
        ):
            failed = True
            raise OSError("fallo del manifiesto")
        real_replace(source, target)

    monkeypatch.setattr(edition_module.os, "replace", fail_manifest)
    with pytest.raises(OSError, match="fallo del manifiesto"):
        write_site_artifacts(pending, publications, complete_only=True)
    assert _artifact_bytes(docs) == before


def test_prepare_dry_run_has_no_filesystem_side_effects(tmp_path: Path) -> None:
    result = prepare_payload(extractive_payload(), force=True)
    assert result["schema_version"] == 8
    assert list(tmp_path.iterdir()) == []


def test_pending_state_cannot_escape_staging(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    payload = extractive_payload()
    payload["_pending_state"] = {"../../../escaped": {"secret": True}}
    with pytest.raises(PublishError, match="estado inseguro"):
        write_site_artifacts_legacy(payload, docs / "data/publications.json")
    assert not (docs / "escaped.json").exists()
    assert not (tmp_path / "escaped.json").exists()


def test_item_outside_mobile_window_keeps_complete_note_and_evidence(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications = docs / "data/publications.json"
    initial = extractive_payload()
    initial["items"][0]["official_evidence"] = {
        "primary_url": "https://dof.gob.mx/nota?id=abc",
        "annex_url": "https://dof.gob.mx/nota_detalle_popup.php?id=abc",
    }
    write_site_artifacts_legacy(initial, publications)
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
    write_site_artifacts_legacy(later, publications)

    permanent = json.loads(
        (docs / "data/items" / f"{item_key(completed['id'])}.json").read_text()
    )["item"]
    archive = json.loads((docs / "data/archive/2026-08.json").read_text())
    assert permanent["editorial_status"] == "complete"
    assert permanent["summary"].split()[0] == "resumen0"
    assert permanent["official_evidence"]["annex_url"].endswith("id=abc")
    assert archive["items"][0]["detail_url"] == completed["detail_url"]
    assert detail_path.exists()
    assert "SHCP modifica" in detail_path.read_text(encoding="utf-8")
