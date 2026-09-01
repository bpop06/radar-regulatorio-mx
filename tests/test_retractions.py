from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.edition import item_key, load_manifested_items, write_site_artifacts_legacy
from app.retractions import RetractionError, restore_items, retract_items
from app.validation import validate_site_artifacts
from tests.test_contract_v8 import extractive_payload


def _two_items() -> dict:
    payload = extractive_payload()
    second = deepcopy(payload["items"][0])
    second.update(
        {
            "id": "CPI:false-positive",
            "source": "CPI",
            "source_id": "false-positive",
            "url": "https://www.icc-cpi.int/vacancy/example",
            "canonical_url": "https://www.icc-cpi.int/vacancy/example",
            "official_title": "Vacancy",
            "official_identifiers": {"source_id": "false-positive"},
            "official_evidence": {
                "primary_url": "https://www.icc-cpi.int/vacancy/example"
            },
        }
    )
    payload["items"].append(second)
    payload["total_items"] = 2
    return payload


def _ledger(tmp_path: Path) -> Path:
    return tmp_path / "private-state/retractions.json"


def test_retract_and_restore_are_transactional_and_keep_reason(tmp_path: Path) -> None:
    publications = tmp_path / "docs/data/publications.json"
    write_site_artifacts_legacy(_two_items(), publications)
    key = item_key("CPI:false-positive")

    assert retract_items(
        publications,
        ["CPI:false-positive"],
        reason="Vacante laboral, no novedad regulatoria.",
        ledger_path=_ledger(tmp_path),
    ) == 1
    assert "CPI:false-positive" not in load_manifested_items(publications)
    assert not (tmp_path / f"docs/data/items/{key}.json").exists()
    assert not (tmp_path / f"docs/notas/{key}.html").exists()
    state = json.loads(
        (tmp_path / "docs/data/state/retractions.json").read_text(encoding="utf-8")
    )
    retracted_at = state["state"]["items"]["CPI:false-positive"]["retracted_at"]
    assert state["state"] == {
        "version": 2,
        "items": {"CPI:false-positive": {"retracted_at": retracted_at}},
    }
    private = json.loads(_ledger(tmp_path).read_text(encoding="utf-8"))
    assert (
        private["items"]["CPI:false-positive"]["reason"]
        == "Vacante laboral, no novedad regulatoria."
    )
    assert "record" in private["items"]["CPI:false-positive"]
    assert validate_site_artifacts(publications.with_name("manifest.json")).ok

    assert restore_items(
        publications, ["CPI:false-positive"], ledger_path=_ledger(tmp_path)
    ) == 1
    assert "CPI:false-positive" in load_manifested_items(publications)
    assert (tmp_path / f"docs/data/items/{key}.json").exists()
    assert (tmp_path / f"docs/notas/{key}.html").exists()
    assert validate_site_artifacts(publications.with_name("manifest.json")).ok


def test_retraction_rejects_missing_id_without_writing(tmp_path: Path) -> None:
    publications = tmp_path / "docs/data/publications.json"
    write_site_artifacts_legacy(_two_items(), publications)
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(RetractionError, match="no presentes"):
        retract_items(publications, ["missing"], reason="No corresponde")

    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_restore_rejects_tampered_retraction_state(tmp_path: Path) -> None:
    publications = tmp_path / "docs/data/publications.json"
    write_site_artifacts_legacy(_two_items(), publications)
    retract_items(
        publications,
        ["CPI:false-positive"],
        reason="Vacante laboral, no novedad regulatoria.",
        ledger_path=_ledger(tmp_path),
    )
    state_path = tmp_path / "docs/data/state/retractions.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["state"]["items"]["CPI:false-positive"]["retracted_at"] = "alterado"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(RetractionError, match="corte base no es válido"):
        restore_items(publications, ["CPI:false-positive"], ledger_path=_ledger(tmp_path))


def test_restore_rejects_uncommitted_retraction_state(tmp_path: Path) -> None:
    publications = tmp_path / "docs/data/publications.json"
    write_site_artifacts_legacy(_two_items(), publications)
    retract_items(
        publications,
        ["CPI:false-positive"],
        reason="Vacante laboral, no novedad regulatoria.",
        ledger_path=_ledger(tmp_path),
    )
    manifest_path = tmp_path / "docs/data/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"].pop("data/state/retractions.json")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RetractionError, match="corte base no es válido"):
        restore_items(publications, ["CPI:false-positive"], ledger_path=_ledger(tmp_path))
