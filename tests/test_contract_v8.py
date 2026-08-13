from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta

from app.edition import prepare_payload
from app.models import Candidate
from app.pipeline import _deduplicate
from app.validation import validate_freshness, validate_publications_payload


def extractive_payload(*, content_hash: str | None = None) -> dict:
    item = {
        "id": "DOF:abc",
        "source_id": "abc",
        "source": "DOF",
        "url": "https://dof.gob.mx/nota?id=abc",
        "canonical_url": "https://dof.gob.mx/nota?id=abc",
        "detail_url": "ficha.html?id=DOF%3Aabc",
        "official_title": "Acuerdo oficial",
        "title": "Título determinista legado",
        "summary": "Resumen legado que no fue certificado por la capa editorial v8.",
        "description": "La autoridad publicó un acuerdo oficial.",
        "detail_markdown": "# Ficha legado",
        "card_body": "## Qué se publicó\n\nAcuerdo.",
        "published_at": "2026-08-12",
        "authority": "SHCP",
        "document_type": "Acuerdo",
        "issuing_body": "SHCP",
        "government_branch": "Ejecutivo federal",
        "jurisdiction": "nacional",
        "country_or_org": "México",
        "published_year": 2026,
        "published_month": 8,
        "published_day": 12,
        "categories": ["Fiscal"],
        "topic_tags": ["Fiscal"],
        "subtopic_tags": [],
        "importance": 3,
        "relevance_score": 4,
        "ai_generated": False,
    }
    if content_hash is not None:
        item["content_hash"] = content_hash
    return {
        "generated_at": "2026-08-12T16:30:00+00:00",
        "lookback_days": 31,
        "total_items": 1,
        "sources": [
            {
                "source": "DOF",
                "status": "ok",
                "items_found": 1,
                "attempts": 1,
                "error": None,
            }
        ],
        "items": [item],
    }


def test_prepare_payload_migrates_v7_without_inheriting_unverified_editorial() -> None:
    payload = extractive_payload()
    payload["items"][0]["case_facts"] = "Interpretación antigua no certificada."
    payload["items"][0]["case_claim"] = "Litis tomada de la ficha oficial."
    payload["items"][0]["case_treaty"] = "CPTPP"
    payload["items"][0]["case_status"] = "Pending"
    result = prepare_payload(payload, force=True)
    item = result["items"][0]

    assert result["schema_version"] == 8
    assert item["source_id"] == "abc"
    assert item["official_published_at"] == "2026-08-12"
    assert item["detected_at"] == result["generated_at"]
    assert item["first_seen_at"] == item["last_seen_at"]
    assert len(item["content_hash"]) == 64
    assert item["editorial_status"] == "needs_review"
    assert item["title"] is None
    assert item["summary"] is None
    assert item["card_body"] is None
    assert item["case_facts"] == ""
    assert item["case_claim"] == "Litis tomada de la ficha oficial."
    assert item["case_treaty"] == "CPTPP"
    assert item["case_status"] == "Pending"
    assert item["official_evidence"]["primary_url"] == item["canonical_url"]
    assert validate_publications_payload(result).ok


def test_deduplication_keeps_same_title_and_date_when_source_ids_differ() -> None:
    common = {
        "source": "DOF",
        "url": "https://dof.gob.mx/nota",
        "official_title": "Acuerdo con el mismo título",
        "description": "Descripción",
        "published_at": datetime(2026, 8, 12, tzinfo=UTC).date(),
    }
    candidates = [
        Candidate(source_id="uno", **common),
        Candidate(source_id="dos", **common),
    ]

    assert {candidate.source_id for candidate in _deduplicate(candidates)} == {"uno", "dos"}


def test_deduplication_uses_canonical_url_when_source_id_is_empty() -> None:
    common = {
        "source": "DOF",
        "source_id": "",
        "official_title": "Acuerdo",
        "published_at": datetime(2026, 8, 12, tzinfo=UTC).date(),
    }
    candidates = [
        Candidate(
            url="https://dof.gob.mx/nota?utm_source=mail&id=1",
            canonical_url="https://dof.gob.mx/nota?id=1",
            description="Breve",
            **common,
        ),
        Candidate(
            url="https://dof.gob.mx/nota?id=1",
            canonical_url="https://dof.gob.mx/nota?id=1",
            description="Descripción oficial más extensa",
            **common,
        ),
    ]

    result = _deduplicate(candidates)
    assert len(result) == 1
    assert result[0].description.startswith("Descripción oficial")


def test_v8_rejects_complete_editorial_outside_word_contract() -> None:
    payload = prepare_payload(extractive_payload(), force=True)
    item = payload["items"][0]
    item.update(
        {
            "editorial_status": "complete",
            "review_reason": None,
            "title": "SHCP modifica reglas aplicables",
            "summary_teaser": "Teaser breve.",
            "summary": "demasiado corto",
            "card_body": (
                "## Qué se publicó\n\nAcuerdo de SHCP.\n\n"
                "## Sustancia\n\nCambio concreto.\n\n"
                "## Fuente\n\n[Abrir fuente](https://dof.gob.mx/nota?id=abc)"
            ),
        }
    )
    report = validate_publications_payload(payload)

    assert not report.ok
    assert any("summary has 2 words" in error for error in report.errors)


def test_v8_complete_editorial_requires_https_official_evidence() -> None:
    payload = prepare_payload(extractive_payload(), force=True)
    item = payload["items"][0]
    item.update(
        {
            "editorial_status": "complete",
            "review_reason": None,
            "title": "SHCP modifica reglas aplicables",
            "summary_teaser": "Teaser breve.",
            "summary": " ".join(f"palabra{i}" for i in range(300)),
            "card_body": (
                "## Qué se publicó\n\nAcuerdo de SHCP.\n\n"
                "## Sustancia\n\nCambio concreto.\n\n"
                "## Fuente\n\nFuente oficial consultada."
            ),
            "official_evidence": {"status": "Pending"},
        }
    )

    report = validate_publications_payload(payload)

    assert not report.ok
    assert any(
        "official_evidence must contain an official HTTPS URL" in error
        for error in report.errors
    )


def test_freshness_is_opt_in_and_timezone_aware() -> None:
    payload = extractive_payload()
    generated = datetime.fromisoformat(payload["generated_at"])

    assert validate_freshness(
        payload, now=generated + timedelta(minutes=89), max_age_hours=1.5
    ) == []
    assert "maximum 1.5 hours" in validate_freshness(
        payload, now=generated + timedelta(minutes=91), max_age_hours=1.5
    )[0]


def test_supplied_invalid_content_hash_is_recomputed() -> None:
    item = prepare_payload(extractive_payload(content_hash="not-a-hash"), force=True)["items"][0]
    assert len(item["content_hash"]) == 64
    int(item["content_hash"], 16)
    assert item["content_hash"] != hashlib.sha256(b"not-a-hash").hexdigest()


def test_supplied_valid_looking_hash_cannot_hide_changed_official_content() -> None:
    original = prepare_payload(extractive_payload(), force=True)["items"][0]
    changed = extractive_payload(content_hash=original["content_hash"])
    changed["items"][0]["official_title"] = "Acuerdo oficial sustituido"

    normalized = prepare_payload(changed, force=True)["items"][0]

    assert normalized["content_hash"] != original["content_hash"]


def test_international_case_reasoning_has_a_separate_roundtrip_field() -> None:
    payload = extractive_payload()
    payload["items"][0].update(
        {
            "jurisdiction": "internacional",
            "country_or_org": "CIADI",
            "case_number": "ARB/26/19",
            "case_outcome": "Pendiente",
            "case_reasoning": "La fuente todavía no publica razonamiento del tribunal.",
        }
    )
    item = prepare_payload(payload, force=True)["items"][0]

    assert item["case_number"] == "ARB/26/19"
    assert item["case_outcome"] == "Pendiente"
    assert item["case_reasoning"].startswith("La fuente")


def test_today_uses_detection_date_without_rewriting_official_date() -> None:
    payload = extractive_payload()
    payload["items"][0]["published_at"] = "2026-04-27"
    payload["items"][0]["official_published_at"] = "2026-04-27"
    payload["items"][0]["detected_at"] = "2026-08-12T16:30:00+00:00"

    result = prepare_payload(payload, force=True)

    assert result["items"][0]["official_published_at"] == "2026-04-27"
    assert result["edition"]["edition_date"] == "2026-08-12"
    assert result["edition"]["total_today"] == 1
    assert result["edition"]["lead_id"] == result["items"][0]["id"]
    assert validate_publications_payload(result).ok


def _stateful_case_payload(source: str) -> dict:
    payload = extractive_payload()
    item = payload["items"][0]
    if source == "CIADI":
        case_number = "ARB/26/19"
        url = (
            "https://icsid.worldbank.org/cases/case-database/case-detail"
            "?CaseNo=ARB%2F26%2F19"
        )
        item.update(
            {
                "id": f"ciadi:{case_number}",
                "source": source,
                "source_id": case_number,
                "url": url,
                "canonical_url": url,
                "case_number": case_number,
                "case_status": "Pending",
                "case_treaty": "CPTPP",
                "official_identifiers": {
                    "source_id": case_number,
                    "case_number": case_number,
                },
                "official_evidence": {
                    "primary_url": url,
                    "case_detail": url,
                    "status": "Pending",
                    "instrument_invoked": "CPTPP",
                },
            }
        )
    else:
        case_number = "MEX-USA-2024-31A-04"
        url = (
            "https://can-mex-usa-sec.org/secretariat/dispute-differends-controversias/"
            "chapter-chapitre-capitulo_31.aspx?lang=eng&case=MEX-USA-2024-31A-04"
        )
        item.update(
            {
                "id": f"secretariado t-mec:{case_number}",
                "source": source,
                "source_id": case_number,
                "url": url,
                "canonical_url": url,
                "case_number": case_number,
                "case_status": "Active",
                "official_identifiers": {
                    "source_id": case_number,
                    "case_number": case_number,
                },
                "official_evidence": {
                    "primary_url": url,
                    "case_listing": url,
                    "status": "Active",
                    "panel_composition": "Adolfo Ciudad, Chair",
                },
            }
        )
    return prepare_payload(payload, force=True)


def test_stateful_source_contract_accepts_official_case_identity() -> None:
    assert validate_publications_payload(_stateful_case_payload("CIADI")).ok
    assert validate_publications_payload(_stateful_case_payload("Secretariado T-MEC")).ok


def test_stateful_source_contract_blocks_legacy_column_and_identity_regressions() -> None:
    tmec = _stateful_case_payload("Secretariado T-MEC")
    tmec["items"][0]["case_status"] = "Adolfo Ciudad, Chair"
    report = validate_publications_payload(tmec)
    assert any("recognized T-MEC status" in error for error in report.errors)

    tmec = _stateful_case_payload("Secretariado T-MEC")
    tmec["items"][0]["canonical_url"] = tmec["items"][0]["canonical_url"].split("?")[0]
    report = validate_publications_payload(tmec)
    assert any("canonical_url must identify its T-MEC case" in error for error in report.errors)

    ciadi = _stateful_case_payload("CIADI")
    ciadi["items"][0]["case_number"] = ""
    report = validate_publications_payload(ciadi)
    assert any("case_number is required for CIADI" in error for error in report.errors)

    ciadi = _stateful_case_payload("CIADI")
    ciadi["items"][0]["case_treaty"] = "T-MEC/TLCAN"
    report = validate_publications_payload(ciadi)
    assert any("instrument_invoked" in error for error in report.errors)


def test_needs_review_rejects_legacy_case_facts() -> None:
    payload = deepcopy(_stateful_case_payload("CIADI"))
    payload["items"][0]["case_facts"] = "Interpretación legacy sin evidencia certificada."

    report = validate_publications_payload(payload)

    assert any("case_facts must be empty while needs_review" in error for error in report.errors)
