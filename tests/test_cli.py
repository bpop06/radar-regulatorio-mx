from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from app import cli
from app.sources.certification import RELAUNCH_SOURCES
from tests.test_contract_v8 import extractive_payload


def _source_results() -> list[dict[str, Any]]:
    return [
        {
            "source": source,
            "status": "ok",
            "items_found": 0,
            "details": {"validated_endpoints": 1},
        }
        for source in RELAUNCH_SOURCES
    ]


def test_collect_parser_exposes_one_shot_stateful_history_rebuild() -> None:
    args = cli.build_parser().parse_args(
        ["collect", "--dry-run", "--rebuild-stateful-history"]
    )

    assert args.dry_run is True
    assert args.rebuild_stateful_history is True


def test_certify_sources_reports_ready_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[int | None, bool]] = []

    async def fake_collect(_settings: object, days: int | None, dry_run: bool) -> dict:
        calls.append((days, dry_run))
        return {"sources": _source_results()}

    monkeypatch.setattr(cli, "collect", fake_collect)
    monkeypatch.setattr(sys, "argv", ["radar-regulatorio", "certify-sources", "--days", "14"])

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert calls == [(14, True)]
    assert output["relaunch_ready"] is True
    assert output["counts"] == {
        "certified": 18,
        "degraded": 0,
        "error": 0,
        "missing": 0,
    }


def test_certify_sources_exits_one_when_a_source_is_degraded(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    results = _source_results()
    results[0] = {
        "source": RELAUNCH_SOURCES[0],
        "status": "degraded",
        "items_found": 0,
        "details": {"warnings": ["estructura inesperada"]},
    }

    async def fake_collect(_settings: object, days: int | None, dry_run: bool) -> dict:
        assert days is None
        assert dry_run is True
        return {"sources": results}

    monkeypatch.setattr(cli, "collect", fake_collect)
    monkeypatch.setattr(sys, "argv", ["radar-regulatorio", "certify-sources"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exc_info.value.code == 1
    assert report["relaunch_ready"] is False
    assert report["counts"]["degraded"] == 1
    assert "Relanzamiento bloqueado" in captured.err
    assert "DOF: degraded" in captured.err


def test_validate_require_v8_rejects_legacy_payload_without_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    publications = tmp_path / "publications.json"
    publications.write_text(json.dumps(extractive_payload()), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["radar-regulatorio", "validate", "--input", str(publications), "--require-v8"],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    assert "schema_version 8" in capsys.readouterr().err
