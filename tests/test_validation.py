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
                "summary": summary or " ".join(f"palabra{i}" for i in range(45)),
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


def test_validate_publications_payload_rejects_summary_below_range():
    report = validate_publications_payload(valid_payload(summary="Resumen demasiado corto."))

    assert not report.ok
    assert any("items[0].summary has 3 words" in error for error in report.errors)


def test_validate_publications_payload_rejects_summary_above_range():
    report = validate_publications_payload(
        valid_payload(summary=" ".join(f"palabra{i}" for i in range(81)))
    )

    assert not report.ok
    assert any("items[0].summary has 81 words" in error for error in report.errors)


def test_validate_publications_payload_accepts_summary_at_bounds():
    assert validate_publications_payload(
        valid_payload(summary=" ".join(f"palabra{i}" for i in range(40)))
    ).ok
    assert validate_publications_payload(
        valid_payload(summary=" ".join(f"palabra{i}" for i in range(80)))
    ).ok


def test_validate_publications_payload_rejects_act_number_in_what_published():
    payload = valid_payload()
    payload["items"][0]["card_body"] = (
        "## Qué se publicó\n\n"
        "Oficio 500-05-2026-16021 de la Secretaría.\n\n"
        "## Sustancia\n\nCambio sustantivo concreto.\n\n"
        "## Fuente\n\n[Abrir publicación oficial](https://dof.gob.mx/nota_detalle.php?codigo=1)"
    )

    report = validate_publications_payload(payload)

    assert not report.ok
    assert any("must not contain an act number" in error for error in report.errors)


def test_validate_publications_payload_accepts_optional_case_fields():
    payload = valid_payload()
    payload["items"][0]["case_parties"] = "México c. Empresa"
    payload["items"][0]["case_status"] = "En trámite"

    assert validate_publications_payload(payload).ok


def test_validate_publications_payload_rejects_non_string_case_fields():
    payload = valid_payload()
    payload["items"][0]["case_status"] = 5

    report = validate_publications_payload(payload)

    assert not report.ok
    assert any("case_status must be str" in error for error in report.errors)


def test_validate_publications_payload_rejects_bad_detail_url():
    payload = valid_payload()
    payload["items"][0]["detail_url"] = "https://example.com/ficha"

    report = validate_publications_payload(payload)

    assert not report.ok
    assert "items[0].detail_url must point to ficha.html" in report.errors


def test_validate_publications_payload_accepts_optional_case_facts():
    payload = valid_payload()
    payload["items"][0]["case_facts"] = "México y la demandante suscribieron un contrato en 2010."

    assert validate_publications_payload(payload).ok


def test_validate_publications_payload_rejects_non_string_case_facts():
    payload = valid_payload()
    payload["items"][0]["case_facts"] = 5

    report = validate_publications_payload(payload)

    assert not report.ok
    assert any("case_facts must be str" in error for error in report.errors)


def test_validate_publications_payload_warns_on_legacy_detail_markdown_sections():
    # El fixture usa la ficha v1 ("## Resumen ejecutivo"); el corte publicado
    # antes del contrato v6 no debe invalidarse por esto, solo advertirse.
    report = validate_publications_payload(valid_payload())

    assert report.ok
    assert any("detail_markdown is missing section" in warning for warning in report.warnings)


def test_validate_publications_payload_rejects_detail_markdown_without_level1_heading():
    payload = valid_payload()
    payload["items"][0]["detail_markdown"] = "Sin encabezado de nivel 1"

    report = validate_publications_payload(payload)

    assert not report.ok
    assert any("must start with a level 1 heading" in error for error in report.errors)


def test_validate_publications_payload_accepts_v2_detail_markdown_without_warnings():
    payload = valid_payload()
    payload["items"][0]["detail_markdown"] = (
        "# Delegan facultades administrativas\n\n"
        "**Fecha:** 2026-07-03 · **Fuente:** DOF · **Órgano:** Secretaría de Economía\n\n"
        "## Qué se publicó\n\nAcuerdo de la SE.\n\n"
        "## Sustancia\n\nDelegación de facultades administrativas.\n\n"
        "## Clasificación\n\n**Materias:** Derecho administrativo.\n\n"
        "## Fuente oficial\n\n*Acuerdo por el que se delegan facultades.*\n\n"
        "[Abrir documento oficial](https://dof.gob.mx/nota_detalle.php?codigo=1)"
    )

    report = validate_publications_payload(payload)

    assert report.ok
    assert report.warnings == []


def test_validate_publications_payload_accepts_valid_digest():
    payload = valid_payload()
    payload["digest"] = {
        "groups": [
            {
                "label": "Fiscal",
                "items": [
                    {
                        "id": "dof:1",
                        "organ": "SE",
                        "theme": " ".join(f"tema{i}" for i in range(12)),
                    }
                ],
            }
        ]
    }

    assert validate_publications_payload(payload).ok


def test_validate_publications_payload_rejects_digest_with_unknown_id():
    payload = valid_payload()
    payload["digest"] = {
        "groups": [
            {
                "label": "Fiscal",
                "items": [
                    {
                        "id": "dof:999",
                        "organ": "SE",
                        "theme": " ".join(f"tema{i}" for i in range(12)),
                    }
                ],
            }
        ]
    }

    report = validate_publications_payload(payload)

    assert not report.ok
    assert any("does not exist in publications" in error for error in report.errors)


def test_validate_publications_payload_rejects_digest_theme_out_of_range():
    payload = valid_payload()
    payload["digest"] = {
        "groups": [{"label": "Fiscal", "items": [{"id": "dof:1", "organ": "SE", "theme": "corto"}]}]
    }

    report = validate_publications_payload(payload)

    assert not report.ok
    assert any("theme has" in error for error in report.errors)


def test_validate_publications_payload_rejects_digest_theme_with_act_number():
    payload = valid_payload()
    payload["digest"] = {
        "groups": [
            {
                "label": "Fiscal",
                "items": [
                    {
                        "id": "dof:1",
                        "organ": "SE",
                        "theme": (
                            "Oficio 500-05-2026-16021 sobre el listado correspondiente "
                            "de contribuyentes"
                        ),
                    }
                ],
            }
        ]
    }

    report = validate_publications_payload(payload)

    assert not report.ok
    assert any("must not contain an act number" in error for error in report.errors)


def test_validate_publications_payload_rejects_digest_with_duplicate_id():
    theme = " ".join(f"tema{i}" for i in range(12))
    payload = valid_payload()
    payload["digest"] = {
        "groups": [
            {
                "label": "Fiscal",
                "items": [
                    {"id": "dof:1", "organ": "SE", "theme": theme},
                    {"id": "dof:1", "organ": "SE", "theme": theme},
                ],
            }
        ]
    }

    report = validate_publications_payload(payload)

    assert not report.ok
    assert any("duplicated in the digest" in error for error in report.errors)


def test_validate_publications_payload_rejects_empty_digest_groups():
    payload = valid_payload()
    payload["digest"] = {"groups": []}

    report = validate_publications_payload(payload)

    assert not report.ok
    assert any("digest.groups must be a non-empty list" in error for error in report.errors)


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
