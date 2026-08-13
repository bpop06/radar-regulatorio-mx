from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

import app.edition as edition_module
from app import cli
from app.edition import item_key, load_manifested_items, write_site_artifacts
from app.international_history import (
    enrich_manifested_international_item,
    rebuild_manifested_international_history,
)
from app.validation import validate_site_artifacts
from tests.test_contract_v8 import extractive_payload

CIJ_PRESS_ID = "cij:bd9268436d6af9d3"
CIJ_PLEADING_ID = "cij:bae7278dc7ce0e5b"
CPI_ID = "cpi:7d10d0a6ac66ac70"
GENERIC_CPI_ID = "cpi:0000000000000000"

CIJ_CASE_BLOCK = (
    "Document Number 200-20260630-PRE-01-00-EN Document Type press_release "
    "Case 200 - Alleged Smuggling of Migrants (Lithuania v. Belarus) "
    "Number (Press Release, Order, etc) 2026/18 Date of the Document "
    "Tue, 06/30/2026 - 12:00"
)
CPI_CASE_TEXT = (
    "The Trust Fund has completed the implementation of the reparations programme in the "
    "case of The Prosecutor v. Ahmad Al Faqi Al Mahdi. Mr Al Mahdi was convicted for the "
    "war crime of intentionally directing attacks against historic and religious monuments "
    "in Timbuktu in 2012. ICC judges instructed him to pay EUR 2.7 million for reparations "
    "to the victims."
)


def _item(
    item_id: str,
    *,
    source: str,
    url: str,
    title: str,
    description: str,
    published_at: str,
    complete: bool = False,
) -> dict:
    value = deepcopy(extractive_payload()["items"][0])
    source_id = item_id.split(":", 1)[1]
    value.update(
        {
            "id": item_id,
            "source_id": source_id,
            "source": source,
            "url": url,
            "canonical_url": url,
            "official_title": title,
            "description": description,
            "published_at": published_at,
            "official_identifiers": {"source_id": source_id},
            "official_evidence": {"primary_url": url},
            "authority": (
                "Corte Internacional de Justicia"
                if source == "CIJ"
                else "Corte Penal Internacional"
            ),
            "document_type": "Comunicado",
            "issuing_body": source,
            "government_branch": "Organismo internacional",
            "jurisdiction": "internacional",
            "country_or_org": source,
            "case_number": "",
            "case_parties": "",
            "case_status": "",
            "case_facts": "",
            "case_treaty": "",
            "case_claim": "",
            "case_outcome": "",
            "case_reasoning": "",
            "case_amount": "",
        }
    )
    if complete:
        value.update(
            {
                "editorial_status": "complete",
                "review_reason": None,
                "title": "CPI concluye un programa de reparaciones en el caso Al Mahdi",
                "summary_teaser": "La CPI informó la conclusión del programa oficial.",
                "summary": " ".join(f"resumen{i}" for i in range(320)),
                "card_body": (
                    "## Qué se publicó\n\nLa CPI informó una actuación.\n\n"
                    "## Sustancia\n\nLa evidencia oficial detalla el programa.\n\n"
                    f"## Fuente\n\n[Abrir fuente]({url})"
                ),
                "detail_markdown": "# Nota editorial completa",
                "ai_generated": True,
            }
        )
    return value


def _historical_cut(tmp_path: Path) -> Path:
    publications = tmp_path / "docs/data/publications.json"
    items = [
        _item(
            CIJ_PRESS_ID,
            source="CIJ",
            url="https://www.icj-cij.org/node/206437",
            title=(
                "Poland files a declaration of intervention in the proceedings under "
                "Article 63 of the Statute of the Court"
            ),
            description=CIJ_CASE_BLOCK,
            published_at="2026-06-30",
        ),
        _item(
            CIJ_PLEADING_ID,
            source="CIJ",
            url="https://www.icj-cij.org/node/206436",
            title="Declaration of intervention of Poland",
            description=CIJ_CASE_BLOCK.replace("press_release", "written_pleading"),
            published_at="2026-06-30",
        ),
        _item(
            CPI_ID,
            source="CPI",
            url="https://www.icc-cpi.int/news/al-mahdi-reparations",
            title="ICC Trust Fund for Victims Completes Reparations Programme in Mali",
            description=CPI_CASE_TEXT,
            published_at="2026-07-02",
            complete=True,
        ),
        _item(
            GENERIC_CPI_ID,
            source="CPI",
            url="https://www.icc-cpi.int/news/trust-fund-contribution",
            title="Trust Fund receives a voluntary contribution",
            description="A State contributed EUR 5 million to support future activities.",
            published_at="2026-07-03",
        ),
    ]
    initial = {
        "generated_at": "2026-08-12T16:30:00+00:00",
        "lookback_days": 31,
        "total_items": len(items),
        "sources": [
            {"source": "CIJ", "status": "ok", "items_found": 2, "attempts": 1},
            {"source": "CPI", "status": "ok", "items_found": 2, "attempts": 1},
        ],
        "items": items,
    }
    write_site_artifacts(initial, publications)

    later = deepcopy(initial)
    later["generated_at"] = "2026-10-01T16:30:00+00:00"
    later["items"] = []
    later["total_items"] = 0
    for source in later["sources"]:
        source["items_found"] = 0
    write_site_artifacts(later, publications)
    return publications


def _artifact_bytes(docs: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(docs): path.read_bytes()
        for path in docs.rglob("*")
        if path.is_file()
    }


def _publish_migration(publications: Path, historical: list[dict]) -> None:
    payload = json.loads(publications.read_text(encoding="utf-8"))
    payload["_historical_items"] = historical
    payload["_preserve_edition"] = True
    write_site_artifacts(payload, publications)


def test_rebuild_international_history_extracts_required_literal_case_fields(
    tmp_path: Path,
) -> None:
    publications = _historical_cut(tmp_path)
    before = load_manifested_items(publications)

    migrated = rebuild_manifested_international_history(publications)
    by_id = {item["id"]: item for item in migrated}

    assert set(by_id) == {CIJ_PRESS_ID, CIJ_PLEADING_ID, CPI_ID}
    for item_id in (CIJ_PRESS_ID, CIJ_PLEADING_ID):
        item = by_id[item_id]
        assert item["case_number"] == "200"
        assert item["case_parties"] == "Lithuania v. Belarus"
        assert item["case_claim"] == "Alleged Smuggling of Migrants"
        assert "intervention" in item["case_status"]
        assert item["official_identifiers"]["case_number"] == "200"
        assert item["official_evidence"]["case_number"] == "200"
        assert item["content_hash"] != before[item_id]["content_hash"]

    cpi = by_id[CPI_ID]
    assert not cpi["case_number"]
    assert cpi["case_parties"] == "The Prosecutor v. Ahmad Al Faqi Al Mahdi"
    assert cpi["case_claim"].startswith("the war crime of intentionally directing attacks")
    assert "completed the implementation of the reparations programme" in cpi["case_status"]
    assert "instructed him to pay EUR 2.7 million" in cpi["case_outcome"]
    assert cpi["case_amount"] == "EUR 2.7 million"
    assert cpi["editorial_status"] == "needs_review"
    assert cpi["title"] is None
    assert cpi["content_hash"] != before[CPI_ID]["content_hash"]


def test_rebuild_does_not_fabricate_case_from_amount_or_unofficial_url(tmp_path: Path) -> None:
    publications = _historical_cut(tmp_path)
    migrated = rebuild_manifested_international_history(publications)

    assert GENERIC_CPI_ID not in {item["id"] for item in migrated}
    foreign = _item(
        "cij:foreign",
        source="CIJ",
        url="https://example.test/node/206437",
        title="Declaration of intervention of Poland",
        description=CIJ_CASE_BLOCK,
        published_at="2026-06-30",
    )
    assert enrich_manifested_international_item(foreign) is None


def test_historical_migration_updates_permanent_window_not_current_cut(tmp_path: Path) -> None:
    publications = _historical_cut(tmp_path)
    before = load_manifested_items(publications)
    historical = rebuild_manifested_international_history(publications)

    _publish_migration(publications, historical)

    current = json.loads(publications.read_text(encoding="utf-8"))
    edition = json.loads(publications.with_name("edition.json").read_text(encoding="utf-8"))
    after = load_manifested_items(publications)
    assert current["items"] == []
    assert current["total_items"] == 0
    assert edition["signals"] == []
    assert after[CIJ_PRESS_ID]["case_number"] == "200"
    assert after[CPI_ID]["case_amount"] == "EUR 2.7 million"
    assert after[CPI_ID]["editorial_status"] == "needs_review"
    assert after[CPI_ID]["content_hash"] != before[CPI_ID]["content_hash"]
    archive = json.loads(
        (publications.parent / "archive/2026-07.json").read_text(encoding="utf-8")
    )
    archived_cpi = next(item for item in archive["items"] if item["id"] == CPI_ID)
    assert archived_cpi["case_amount"] == "EUR 2.7 million"
    assert validate_site_artifacts(publications.with_name("manifest.json")).ok


def test_historical_migration_rolls_back_every_artifact_on_commit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    publications = _historical_cut(tmp_path)
    historical = rebuild_manifested_international_history(publications)
    docs = publications.parent.parent
    before = _artifact_bytes(docs)
    real_replace = edition_module.os.replace
    calls = 0

    def fail_during_commit(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 6:
            raise OSError("fallo internacional simulado")
        real_replace(source, target)

    monkeypatch.setattr(edition_module.os, "replace", fail_during_commit)

    with pytest.raises(OSError, match="fallo internacional simulado"):
        _publish_migration(publications, historical)

    assert calls > 6
    assert _artifact_bytes(docs) == before


def test_rebuild_international_history_cli_dry_run_never_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    publications = _historical_cut(tmp_path)
    docs = publications.parent.parent
    before = _artifact_bytes(docs)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "radar-regulatorio",
            "rebuild-international-history",
            "--input",
            str(publications),
            "--dry-run",
        ],
    )

    cli.main()

    preview = json.loads(capsys.readouterr().out)
    assert preview["dry_run"] is True
    assert preview["total_items"] == 3
    assert _artifact_bytes(docs) == before
    assert not (docs / "data/items" / f"{item_key('missing')}.json").exists()
