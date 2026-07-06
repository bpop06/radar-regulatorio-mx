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
                "published_at": "2026-07-03",
                "authority": "Secretaría de Economía",
                "document_type": "Acuerdo",
                "categories": ["Derecho administrativo"],
                "relevance_score": 3,
                "ai_generated": False,
            }
        ],
    }


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
