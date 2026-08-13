import json
from datetime import date

from app.edition import (
    build_default_edition,
    build_edition_artifact,
    edition_date_from_generated_at,
    prepare_payload,
    write_site_artifacts,
)
from app.text import ACT_NUMBER_RE
from app.validation import validate_edition


def item(
    item_id: str,
    *,
    published_at: str = "2026-07-07",
    importance: int = 3,
    relevance: int = 3,
    body: str = "SAT",
    category: str = "Fiscal",
) -> dict:
    return {
        "id": item_id,
        "title": f"Título editorial {item_id}",
        "summary": " ".join(f"resumen{number}" for number in range(45)),
        "source": "DOF",
        "url": f"https://example.gob.mx/{item_id}",
        "detail_url": f"ficha.html?id={item_id}",
        "published_at": published_at,
        "issuing_body": body,
        "categories": [category],
        "importance": importance,
        "relevance_score": relevance,
        "jurisdiction": "nacional",
        "country_or_org": "México",
        "card_body": "contenido que no debe llegar al artefacto ligero",
    }


def payload(items: list[dict]) -> dict:
    return {
        "generated_at": "2026-07-07T18:00:00+00:00",
        "lookback_days": 31,
        "total_items": len(items),
        "sources": [
            {"source": "DOF", "status": "ok", "items_found": 1, "attempts": 1, "error": None},
            {
                "source": "SNICE",
                "status": "error",
                "items_found": 0,
                "attempts": 3,
                "error": "Timeout",
            },
        ],
        "items": items,
    }


def test_edition_date_uses_mexico_city_timezone():
    assert edition_date_from_generated_at("2026-07-08T03:30:00+00:00") == date(2026, 7, 7)


def test_prepare_payload_builds_ready_v8_edition():
    result = prepare_payload(payload([item("dof:1")]), force=True)

    assert result["schema_version"] == 8
    assert result["edition"]["edition_date"] == "2026-07-07"
    assert result["edition"]["state"] == "ready"
    assert result["edition"]["lead_id"] == "dof:1"
    assert result["edition"]["coverage"] == {
        "state": "degraded",
        "ok": 1,
        "failed": ["SNICE"],
    }
    assert validate_edition(result["edition"], result["items"], result["sources"]) == []


def test_default_edition_does_not_copy_act_number_into_automatic_reason():
    record = item("snice:128")
    record["summary"] = (
        "Acuerdo No. 128 de la Comisión Administradora que comunica una actualización "
        "oficial para las partes involucradas en el tratado comercial."
    )
    source_payload = payload([record])
    assert ACT_NUMBER_RE.search(record["summary"]) is not None

    edition = build_default_edition(source_payload, date(2026, 7, 7))

    reason = edition["signals"][0]["why_it_matters"]
    assert "Acuerdo No. 128" not in reason
    assert ACT_NUMBER_RE.search(reason) is None
    assert validate_edition(edition, source_payload["items"], source_payload["sources"]) == []


def test_default_ranking_limits_signals_and_diversifies_first_pass():
    records = [
        item(
            f"dof:{index}",
            importance=5 if index < 4 else 4,
            relevance=10 - index,
            body="SAT" if index < 4 else f"Órgano {index}",
            category="Fiscal" if index < 4 else f"Materia {index}",
        )
        for index in range(10)
    ]

    edition = build_default_edition(payload(records), date(2026, 7, 7))

    assert len(edition["signals"]) == 7
    selected = {signal["id"] for signal in edition["signals"]}
    assert {"dof:0", "dof:1"}.issubset(selected)
    assert "dof:2" not in selected
    assert edition["signals"][0]["rank"] == 1


def test_default_edition_is_empty_without_items_from_cut_date():
    result = build_default_edition(
        payload([item("dof:old", published_at="2026-07-06")]),
        date(2026, 7, 7),
    )

    assert result["state"] == "empty"
    assert result["total_today"] == 0
    assert result["last_available_date"] == "2026-07-06"
    assert result["lead_id"] is None
    assert result["signals"] == []


def test_edition_artifact_denormalizes_only_public_signal_fields():
    prepared = prepare_payload(payload([item("dof:1")]), force=True)

    artifact = build_edition_artifact(prepared)

    signal = artifact["signals"][0]
    assert signal["title"] is None
    assert signal["official_title"] == "Título editorial dof:1"
    assert signal["editorial_status"] == "needs_review"
    assert signal["rank"] == 1
    assert "why_it_matters" in signal
    assert "card_body" not in signal


def test_write_site_artifacts_writes_both_json_files(tmp_path):
    prepared = prepare_payload(payload([item("dof:1")]), force=True)
    publications_path = tmp_path / "publications.json"

    write_site_artifacts(prepared, publications_path)

    written_publications = json.loads(publications_path.read_text(encoding="utf-8"))
    written_edition = json.loads((tmp_path / "edition.json").read_text(encoding="utf-8"))
    assert written_publications["schema_version"] == 8
    assert written_edition["lead_id"] == "dof:1"
    assert written_publications["cut_id"] == written_edition["cut_id"]
    assert (tmp_path / "manifest.json").exists()
    assert not list(tmp_path.glob("*.tmp"))
