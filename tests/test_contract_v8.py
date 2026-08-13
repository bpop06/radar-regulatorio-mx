from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

from app.edition import detection_date, prepare_payload
from app.models import Candidate
from app.pipeline import _deduplicate, _publication_from_classified
from app.relevance import classify
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
    assert item["detected_at"] == "2026-08-12T12:00:00+00:00"
    assert item["first_seen_at"] <= item["last_seen_at"]
    assert item["last_seen_at"] == result["generated_at"]
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


def test_prepare_payload_defaults_and_preserves_collection_notes() -> None:
    result = prepare_payload(extractive_payload(), force=True)

    assert result["collection_notes"] == {"dof_previous_day_reviewed": False}

    payload = extractive_payload()
    payload["collection_notes"] = {
        "dof_previous_day_reviewed": True,
        "extra_note": "preservada",
    }

    preserved = prepare_payload(payload, force=True)

    assert preserved["collection_notes"] == payload["collection_notes"]


def test_v8_validation_requires_well_typed_collection_notes() -> None:
    missing_notes = prepare_payload(extractive_payload(), force=True)
    missing_notes.pop("collection_notes")

    report = validate_publications_payload(missing_notes)

    assert "collection_notes must be an object" in report.errors

    invalid_note = prepare_payload(extractive_payload(), force=True)
    invalid_note["collection_notes"] = {"dof_previous_day_reviewed": "sí"}

    report = validate_publications_payload(invalid_note)

    assert "collection_notes.dof_previous_day_reviewed must be a boolean" in report.errors


def test_deduplication_keeps_same_title_and_date_when_source_ids_differ() -> None:
    common = {
        "source": "DOF",
        "official_title": "Acuerdo con el mismo título",
        "description": "Descripción",
        "published_at": datetime(2026, 8, 12, tzinfo=UTC).date(),
    }
    candidates = [
        Candidate(source_id="uno", url="https://dof.gob.mx/nota", **common),
        Candidate(source_id="dos", url="https://dof.gob.mx/nota", **common),
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


def test_deduplication_uses_canonical_url_globally_and_prefers_specific_source() -> None:
    common = {
        "url": "https://www.gob.mx/impi/prensa/aviso-oficial?idiom=es",
        "canonical_url": "https://www.gob.mx/impi/prensa/aviso-oficial?idiom=es",
        "official_title": "IMPI publica un aviso oficial",
        "published_at": datetime(2026, 8, 12, tzinfo=UTC).date(),
    }
    candidates = [
        Candidate(
            source="Gob.mx APF",
            source_id="gob-copy",
            description="Descripción extensa recuperada por el agregador.",
            **common,
        ),
        Candidate(
            source="IMPI",
            source_id="impi-copy",
            description="Aviso oficial.",
            **common,
        ),
    ]

    result = _deduplicate(reversed(candidates))

    assert len(result) == 1
    assert result[0].source == "IMPI"
    assert result[0].source_id == "impi-copy"


def test_deduplication_normalizes_query_order_and_tracking_parameters() -> None:
    common = {
        "official_title": "Aviso oficial compartido",
        "description": "Texto oficial.",
        "published_at": date(2026, 8, 12),
    }
    result = _deduplicate(
        [
            Candidate(
                source="SNICE",
                source_id="mirror",
                url="https://dof.gob.mx/nota?b=2&a=1&utm_source=snice",
                **common,
            ),
            Candidate(
                source="DOF",
                source_id="official",
                url="https://dof.gob.mx/nota?a=1&b=2",
                **common,
            ),
        ]
    )

    assert [(item.source, item.source_id) for item in result] == [("DOF", "official")]


def test_deduplication_prefers_dof_over_snice_for_same_official_act() -> None:
    url = "https://dof.gob.mx/nota_detalle.php?codigo=5796065&fecha=12/08/2026"
    common = {
        "url": url,
        "canonical_url": url,
        "official_title": "Acuerdo por el que se da a conocer la Decisión No. 128",
        "description": "Texto oficial del acuerdo.",
        "published_at": datetime(2026, 8, 12, tzinfo=UTC).date(),
    }

    result = _deduplicate(
        [
            Candidate(source="SNICE", source_id="snice-copy", **common),
            Candidate(source="DOF", source_id="5796065", **common),
        ]
    )

    assert [(item.source, item.source_id) for item in result] == [("DOF", "5796065")]


def test_deduplication_preserves_distinct_diputados_anchors() -> None:
    base = "https://gaceta.diputados.gob.mx/Gaceta/66/2026/ago/20260812.html"
    common = {
        "source": "Diputados",
        "official_title": "Que reforma la Ley Aduanera",
        "description": "Iniciativa parlamentaria.",
        "published_at": datetime(2026, 8, 12, tzinfo=UTC).date(),
    }
    candidates = [
        Candidate(source_id="anchor-1", url=f"{base}#Iniciativa1", **common),
        Candidate(source_id="anchor-2", url=f"{base}#Iniciativa2", **common),
    ]

    assert {item.source_id for item in _deduplicate(candidates)} == {
        "anchor-1",
        "anchor-2",
    }


def test_deduplication_collapses_official_impi_press_and_article_routes() -> None:
    title = "IMPI asegura mercancía apócrifa en Aduana de Lázaro Cárdenas"
    common = {
        "official_title": title,
        "description": title,
        "published_at": datetime(2026, 7, 15, tzinfo=UTC).date(),
    }
    press_url = (
        "https://www.gob.mx/impi/prensa/"
        "impi-asegura-mercancia-apocrifa-en-aduana-de-lazaro-cardenas-michoacan?idiom=es"
    )
    article_url = (
        "https://www.gob.mx/impi/es/articulos/"
        "impi-asegura-mercancia-apocrifa-en-aduana-de-lazaro-cardenas-michoacan-430469"
        "?idiom=es"
    )
    candidates = [
        Candidate(source="IMPI", source_id="impi", url=press_url, **common),
        Candidate(source="Gob.mx APF", source_id="gob-press", url=press_url, **common),
        Candidate(source="Gob.mx APF", source_id="gob-article", url=article_url, **common),
    ]

    result = _deduplicate(candidates)

    assert len(result) == 1
    assert result[0].source == "IMPI"


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


def test_legacy_mobile_window_does_not_become_today_inventory() -> None:
    payload = extractive_payload()
    payload["items"] = []
    for offset in range(31):
        record = deepcopy(extractive_payload()["items"][0])
        official_date = date(2026, 8, 12) - timedelta(days=offset)
        record.update(
            {
                "id": f"DOF:{offset}",
                "source_id": str(offset),
                "url": f"https://dof.gob.mx/nota?id={offset}",
                "canonical_url": f"https://dof.gob.mx/nota?id={offset}",
                "published_at": official_date.isoformat(),
            }
        )
        record.pop("detected_at", None)
        payload["items"].append(record)
    payload["total_items"] = len(payload["items"])

    result = prepare_payload(payload, force=True)

    assert result["total_items"] == 31
    assert result["edition"]["total_today"] == 1
    assert [signal["id"] for signal in result["edition"]["signals"]] == ["DOF:0"]


def test_normal_late_detection_counts_today_but_bootstrap_inventory_does_not() -> None:
    generated_at = datetime(2026, 8, 12, 16, 30, tzinfo=UTC)
    old_candidate = Candidate(
        source="DOF",
        source_id="late",
        url="https://dof.gob.mx/nota?id=late",
        official_title="Resolución fiscal detectada con retraso",
        description="Resolución sobre el Código Fiscal.",
        published_at=date(2026, 8, 10),
    )

    normal = _publication_from_classified(
        classify(old_candidate), generated_at, edition_date=date(2026, 8, 12)
    ).to_dict()
    baseline = _publication_from_classified(
        classify(old_candidate),
        generated_at,
        edition_date=date(2026, 8, 12),
        baseline_inventory=True,
    ).to_dict()

    assert detection_date(normal) == "2026-08-12"
    assert normal["detected_at"] == generated_at.isoformat()
    assert detection_date(baseline) == "2026-08-10"
    assert baseline["detected_at"] == "2026-08-10T12:00:00+00:00"


def test_explicit_state_change_survives_bootstrap_baselining() -> None:
    generated_at = datetime(2026, 8, 12, 16, 30, tzinfo=UTC)
    changed = Candidate(
        source="CIADI",
        source_id="ARB/26/19",
        url="https://icsid.worldbank.org/case?CaseNo=ARB%2F26%2F19",
        official_title="Caso CIADI ARB/26/19",
        description="El estatus oficial cambió a Concluded.",
        published_at=date(2026, 4, 27),
        detected_at=generated_at,
        case_number="ARB/26/19",
        case_status="Concluded",
    )

    publication = _publication_from_classified(
        classify(changed),
        generated_at,
        edition_date=date(2026, 8, 12),
        baseline_inventory=True,
    ).to_dict()

    assert publication["official_published_at"] == "2026-04-27"
    assert detection_date(publication) == "2026-08-12"


def test_historical_worldbank_http_urls_are_promoted_during_normalization() -> None:
    payload = extractive_payload()
    payload["items"][0].update(
        {
            "id": "banco mundial:legacy",
            "source": "Banco Mundial",
            "source_id": "legacy",
            "url": "http://www.bancomundial.org/es/news/press-release/legacy",
            "canonical_url": "http://www.bancomundial.org/es/news/press-release/legacy",
            "official_identifiers": {"source_id": "legacy"},
            "official_evidence": {
                "primary_url": "http://www.bancomundial.org/es/news/press-release/legacy"
            },
        }
    )

    item = prepare_payload(payload, force=True)["items"][0]

    assert item["url"].startswith("https://www.bancomundial.org/")
    assert item["canonical_url"].startswith("https://www.bancomundial.org/")
    assert item["official_evidence"]["primary_url"].startswith(
        "https://www.bancomundial.org/"
    )


def test_historical_gobmx_records_refresh_parser_and_taxonomy() -> None:
    cases = [
        (
            "mobile",
            "SAT cierra julio con despliegue de Oficina Móvil en poblaciones alejadas",
            "La iniciativa fue posible por colaboración. La sede está en Reforma Ote. 3.",
        ),
        (
            "expert",
            "Emite Profepa dictamen pericial por saqueo de huevos de tortuga",
            "El dictamen pericial fue solicitado por la Fiscalía General.",
        ),
        (
            "tourism",
            "Sectur y Volaris llevan la campaña México está de Moda a nuevos mercados",
            "Esta iniciativa promocional llevará la marca México está de Moda en una aeronave.",
        ),
    ]
    payload = extractive_payload()
    payload["items"] = []
    for source_id, title, description in cases:
        record = deepcopy(extractive_payload()["items"][0])
        record.update(
            {
                "id": f"gob.mx apf:{source_id}",
                "source": "Gob.mx APF",
                "source_id": source_id,
                "url": f"https://www.gob.mx/portal/prensa/{source_id}",
                "canonical_url": f"https://www.gob.mx/portal/prensa/{source_id}",
                "official_title": title,
                "description": description,
                "categories": ["Proceso legislativo"],
                "topic_tags": ["Iniciativa"],
                "official_identifiers": {"source_id": source_id},
                "official_evidence": {
                    "primary_url": f"https://www.gob.mx/portal/prensa/{source_id}"
                },
            }
        )
        payload["items"].append(record)
    duplicated = deepcopy(extractive_payload()["items"][0])
    duplicated.update(
        {
            "id": "gob.mx apf:duplicated",
            "source": "Gob.mx APF",
            "source_id": "duplicated",
            "official_title": (
                "¿Eres una persona pensionada o jubilada? PRODECON te informa sobre"
                "¿Eres una persona pensionada o jubilada? PRODECON te informa sobre?"
            ),
        }
    )
    payload["items"].append(duplicated)
    payload["total_items"] = len(payload["items"])

    normalized = prepare_payload(payload, force=True)
    by_id = {item["source_id"]: item for item in normalized["items"]}

    assert "Fiscal" in by_id["mobile"]["topic_tags"]
    assert "Iniciativa" not in by_id["mobile"]["topic_tags"]
    assert "Proceso legislativo" not in by_id["mobile"]["categories"]
    assert "Iniciativa" not in by_id["expert"]["topic_tags"]
    assert "Proceso legislativo" not in by_id["expert"]["categories"]
    assert "Iniciativa" not in by_id["tourism"]["topic_tags"]
    assert "Propiedad intelectual" not in by_id["tourism"]["topic_tags"]
    assert "Proceso legislativo" not in by_id["tourism"]["categories"]
    assert by_id["duplicated"]["official_title"] == (
        "¿Eres una persona pensionada o jubilada? PRODECON te informa sobre?"
    )


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
