from app.validation import validate_publications_payload


def valid_payload(summary: str | None = None):
    return {
        "generated_at": "2026-07-03T19:45:14.050304+00:00",
        "lookback_days": 31,
        "total_items": 1,
        "sources": [
            {
                "source": "DOF",
                "status": "ok",
                "items_found": 12,
                "error": None,
                "attempts": 1,
            }
        ],
        "items": [
            {
                "id": "dof:1",
                "source": "DOF",
                "url": "https://dof.gob.mx/nota_detalle.php?codigo=1",
                "detail_url": "ficha.html?id=dof%3A1",
                "official_title": "Acuerdo por el que se delegan facultades.",
                "title": "Delegan facultades administrativas",
                "summary": summary or " ".join(f"palabra{i}" for i in range(30)),
                "description": "El acuerdo delega facultades administrativas específicas.",
                "detail_markdown": (
                    "# Delegan facultades administrativas\n\n"
                    "## Resumen ejecutivo\n\n"
                    "Resumen ejecutivo de prueba.\n\n"
                    "## Información oficial\n\n"
                    "**Título oficial:** Acuerdo por el que se delegan facultades."
                ),
                "card_body": (
                    "## Qué se publicó\n\n"
                    "Acuerdo de la Secretaría de Economía.\n\n"
                    "## Sustancia\n\n"
                    "Delegación de facultades administrativas específicas.\n\n"
                    "## Fuente\n\n"
                    "[Abrir publicación oficial](https://dof.gob.mx/nota_detalle.php?codigo=1)"
                ),
                "published_at": "2026-07-03",
                "authority": "Secretaría de Economía",
                "document_type": "Acuerdo",
                "issuing_body": "Secretaría de Economía",
                "government_branch": "Ejecutivo federal",
                "jurisdiction": "nacional",
                "country_or_org": "México",
                "published_year": 2026,
                "published_month": 7,
                "published_day": 3,
                "categories": ["Derecho administrativo"],
                "topic_tags": ["Derecho administrativo", "Acuerdo"],
                "subtopic_tags": ["delegan facultades"],
                "importance": 2,
                "relevance_score": 3,
                "ai_generated": False,
            }
        ],
    }


def test_validate_publications_payload_rejects_missing_card_body_sections():
    payload = valid_payload()
    payload["items"][0]["card_body"] = "## Qué se publicó\n\nSolo una sección."

    report = validate_publications_payload(payload)

    assert not report.ok
    assert any("card_body is missing section" in error for error in report.errors)


def test_validate_publications_payload_warns_on_office_number_title():
    payload = valid_payload()
    payload["items"][0]["title"] = "Oficio 500-05-2026-16021 comunica listado"

    report = validate_publications_payload(payload)

    assert report.ok
    assert any("starts with an office/act number" in warning for warning in report.warnings)


def test_validate_publications_payload_accepts_valid_contract():
    report = validate_publications_payload(valid_payload())

    assert report.ok
    assert report.errors == []


def test_validate_publications_payload_rejects_wrong_summary_length():
    report = validate_publications_payload(valid_payload(summary="Resumen demasiado corto."))

    assert not report.ok
    assert "items[0].summary has 3 words" in report.errors


def test_validate_publications_payload_rejects_bad_detail_url():
    payload = valid_payload()
    payload["items"][0]["detail_url"] = "https://example.com/ficha"

    report = validate_publications_payload(payload)

    assert not report.ok
    assert "items[0].detail_url must point to ficha.html" in report.errors


def test_validate_publications_payload_rejects_all_sources_in_error():
    payload = valid_payload()
    payload["sources"] = [
        {
            "source": "DOF",
            "status": "error",
            "items_found": 0,
            "error": "ConnectError",
            "attempts": 3,
        }
    ]

    report = validate_publications_payload(payload)

    assert not report.ok
    assert report.source_errors == ["DOF: ConnectError"]
    assert "all sources are in error state" in report.errors
