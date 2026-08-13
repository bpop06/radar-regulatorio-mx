from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app import edition
from app.edition import _state_payloads, write_site_artifacts
from app.sources.icsid import _load_snapshot as load_icsid_snapshot
from app.sources.tmec import _load_snapshot as load_tmec_snapshot
from tests.test_contract_v8 import extractive_payload

LOADERS = {
    "icsid": load_icsid_snapshot,
    "tmec": load_tmec_snapshot,
}


def _write_cut(docs: Path) -> dict:
    payload = extractive_payload()
    payload["_pending_state"] = {
        "icsid": {"ARB/26/19": "Pending"},
        "tmec": {"MEX-USA-2024-31A-04": "Active"},
    }
    return write_site_artifacts(payload, docs / "data/publications.json")


def _rewrite_state_and_hash(docs: Path, name: str, payload: dict) -> None:
    relative = f"data/state/{name}.json"
    path = docs / relative
    content = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    path.write_bytes(content)
    manifest_path = docs / "data/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][relative] = {
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("name", LOADERS)
def test_state_envelope_without_manifest_is_never_consumed(
    tmp_path: Path, name: str
) -> None:
    path = tmp_path / "docs/data/state" / f"{name}.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 8,
                "cut_id": "orphan-cut",
                "source": name,
                "state": {"case": "changed"},
            }
        ),
        encoding="utf-8",
    )

    assert LOADERS[name](path) is None
    assert _state_payloads(path.parent.parent, {}) == {}


@pytest.mark.parametrize("name", LOADERS)
def test_state_with_tampered_bytes_is_rejected(tmp_path: Path, name: str) -> None:
    docs = tmp_path / "docs"
    _write_cut(docs)
    path = docs / "data/state" / f"{name}.json"
    path.write_bytes(path.read_bytes() + b" \n")

    assert LOADERS[name](path) is None
    states = _state_payloads(docs / "data", {})
    assert name not in states


@pytest.mark.parametrize("name", LOADERS)
def test_state_cut_mismatch_is_rejected_even_when_hash_matches(
    tmp_path: Path, name: str
) -> None:
    docs = tmp_path / "docs"
    manifest = _write_cut(docs)
    path = docs / "data/state" / f"{name}.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["cut_id"] = "different-cut"
    _rewrite_state_and_hash(docs, name, state)

    assert manifest["cut_id"] != state["cut_id"]
    assert LOADERS[name](path) is None
    assert name not in _state_payloads(docs / "data", {})


@pytest.mark.parametrize("name", LOADERS)
def test_manifest_committed_state_is_accepted(tmp_path: Path, name: str) -> None:
    docs = tmp_path / "docs"
    _write_cut(docs)
    path = docs / "data/state" / f"{name}.json"

    loaded = LOADERS[name](path)
    states = _state_payloads(docs / "data", {})

    assert loaded is not None
    assert states[name] == loaded


def test_legacy_snapshot_is_only_accepted_before_v8_manifest(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    path = docs / "data/icsid_snapshot.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"ARB/26/19": "Legacy"}), encoding="utf-8")

    assert load_icsid_snapshot(path) == {"ARB/26/19": "Legacy"}
    assert _state_payloads(docs / "data", {})["icsid"] == {
        "ARB/26/19": "Legacy"
    }

    _write_cut(docs)

    assert load_icsid_snapshot(path) is None
    assert _state_payloads(docs / "data", {})["icsid"] == {
        "ARB/26/19": "Pending"
    }


def test_manifest_replace_is_the_commit_marker_and_happens_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = tmp_path / "docs"
    targets: list[Path] = []
    real_replace = edition.os.replace

    def tracked_replace(source: Path, target: Path) -> None:
        targets.append(Path(target))
        real_replace(source, target)

    monkeypatch.setattr(edition.os, "replace", tracked_replace)

    _write_cut(docs)

    assert targets
    assert targets[-1] == docs / "data/manifest.json"
