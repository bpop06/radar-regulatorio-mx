import json

import pytest

from app.edition import item_key, prepare_payload
from app.editorial import EditorialError, apply_editorial
from app.storage import Storage

VALID_TEASER = " ".join(f"avance{i}" for i in range(40))
VALID_SUMMARY = " ".join(f"palabra{i}" for i in range(320))
VALID_CARD_BODY = (
    "## Qué se publicó\n\nAcuerdo de la autoridad.\n\n"
    "## Sustancia\n\nCambio sustantivo concreto.\n\n"
    "## Fuente\n\n[Abrir publicación oficial](https://example.gob.mx/doc)"
)


def permanent_item(pubs, light):
    path = pubs.parent / "items" / f"{item_key(light['id'])}.json"
    return json.loads(path.read_text(encoding="utf-8"))["item"]


def extracted_private_payload() -> dict:
    payload = prepare_payload(publications_payload(), force=True)
    item = payload["items"][0]
    item.update(
        extraction_status="complete",
        extracted_text="Texto oficial completo con inicio, contenido material y final.",
        source_content_hash="b" * 64,
        extraction_method="html-adapter",
        extraction_retrieved_at="2026-08-13T12:00:00+00:00",
        source_sections=[{"id": "p1", "label": "Cuerpo", "order": 1}],
    )
    return payload
CARD_BODY_WITH_ACT_NUMBER = (
    "## Qué se publicó\n\nOficio 500-05-2026-16021 de la autoridad.\n\n"
    "## Sustancia\n\nCambio sustantivo concreto.\n\n"
    "## Fuente\n\n[Abrir publicación oficial](https://example.gob.mx/doc)"
)
CARD_BODY_WITH_YEAR_RANGE = (
    "## Qué se publicó\n\nPrograma institucional para el periodo 2026-2030.\n\n"
    "## Sustancia\n\nCambio sustantivo concreto.\n\n"
    "## Fuente\n\n[Abrir publicación oficial](https://example.gob.mx/doc)"
)


def publications_payload() -> dict:
    return {
        "generated_at": "2026-07-07T10:00:00+00:00",
        "lookback_days": 31,
        "total_items": 1,
        "sources": [
            {"source": "DOF", "status": "ok", "items_found": 1, "attempts": 1, "error": None}
        ],
        "items": [
            {
                "id": "dof:1",
                "source": "DOF",
                "url": "https://example.gob.mx/doc",
                "detail_url": "ficha.html?id=dof%3A1",
                "official_title": "Acuerdo por el que se delegan facultades.",
                "title": "Título extractivo original",
                "summary": VALID_SUMMARY,
                "description": "El acuerdo delega facultades administrativas.",
                "detail_markdown": (
                    "# Título extractivo original\n\n## Resumen ejecutivo\n\nResumen."
                ),
                "card_body": VALID_CARD_BODY,
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


DIGEST_THEME = " ".join(f"tema{i}" for i in range(12))


def edit(**overrides) -> dict:
    base = {
        "id": "dof:1",
        "title": "SHCP actualiza el listado de contribuyentes con presunción fiscal",
        "summary_teaser": VALID_TEASER,
        "summary": VALID_SUMMARY,
        "card_body": VALID_CARD_BODY,
    }
    base.update(overrides)
    return base


def private_edit(**overrides) -> dict:
    base = edit(
        source_content_hash="b" * 64,
        executive_summary=["La autoridad modificó una obligación administrativa."],
        detailed_summary=[{
            "heading": "Cambio principal",
            "paragraphs": ["El acuerdo establece la medida descrita."],
            "evidence_refs": ["p1"],
        }],
        impacts={
            "general": {
                "paragraphs": ["No existe impacto general directo."],
                "evidence_refs": ["p1"],
            },
            "sectors": [{
                "sector": "Personas reguladas",
                "paragraphs": ["Deben revisar la medida aplicable."],
                "evidence_refs": ["p1"],
            }],
        },
        recommended_actions=[{
            "affected_group": "Personas reguladas",
            "paragraphs": ["Conservar la documentación relacionada."],
            "trigger": "Quedar sujeto al acuerdo",
            "deadline": None,
            "legal_basis": None,
            "evidence_refs": ["p1"],
        }],
        evidence={
            "official_url": "https://example.gob.mx/doc",
            "retrieved_at": "2026-08-13T12:00:00+00:00",
            "content_hash": "b" * 64,
            "extraction_method": "html-adapter",
            "locators": [{"id": "p1", "label": "Cuerpo", "location": "párrafo 1"}],
        },
        coverage=[{
            "source_ref": "p1", "disposition": "summarized",
            "target_sections": ["detailed_summary"], "reason": None,
        }],
    )
    base.update(overrides)
    return base


def digest_block(**item_overrides) -> dict:
    item = {"id": "dof:1", "organ": "SHCP", "theme": DIGEST_THEME}
    item.update(item_overrides)
    return {"groups": [{"label": "Fiscal", "items": [item]}]}


def edition_block() -> dict:
    return {
        "edition_date": "2026-07-07",
        "state": "ready",
        "coverage": {"state": "complete", "ok": 1, "total": 1, "failed": []},
        "total_today": 1,
        "last_available_date": "2026-07-07",
        "lead_id": "dof:1",
        "signals": [
            {
                "id": "dof:1",
                "rank": 1,
                "why_it_matters": (
                    "La actualización modifica obligaciones fiscales relevantes para "
                    "contribuyentes y exige revisar de inmediato la fuente oficial."
                ),
            }
        ],
    }


def write_files(tmp_path, edits, digest=None):
    pubs = tmp_path / "publications.json"
    payload = prepare_payload(publications_payload(), force=True)
    pubs.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    by_id = {item["id"]: item for item in payload["items"]}
    edits = [dict(edit_value) for edit_value in edits]
    for edit_value in edits:
        item = by_id.get(edit_value.get("id"))
        if item is not None:
            edit_value.setdefault("content_hash", item["content_hash"])
    edits_payload: dict = {"items": edits}
    if digest is not None:
        edits_payload["digest"] = digest
    edits_file = tmp_path / "edits.json"
    edits_file.write_text(json.dumps(edits_payload, ensure_ascii=False), encoding="utf-8")
    return edits_file, pubs


def test_apply_editorial_updates_fields_and_marks_ai_generated(tmp_path):
    edits_file, pubs = write_files(tmp_path, [edit()])

    applied = apply_editorial(edits_file, pubs)

    assert applied == 1
    light = json.loads(pubs.read_text(encoding="utf-8"))["items"][0]
    result = permanent_item(pubs, light)
    assert result["title"].startswith("SHCP actualiza")
    assert result["ai_generated"] is True
    assert result["published_at"] == "2026-07-03"


def test_apply_editorial_accepts_year_range_in_what_published(tmp_path):
    edits_file, pubs = write_files(tmp_path, [edit(card_body=CARD_BODY_WITH_YEAR_RANGE)])

    apply_editorial(edits_file, pubs)

    light = json.loads(pubs.read_text(encoding="utf-8"))["items"][0]
    result = permanent_item(pubs, light)
    assert "2026-2030" in result["card_body"]


def test_apply_editorial_updates_optional_importance(tmp_path):
    # Bug #15: la curaduría editorial necesita poder subir/bajar la
    # importancia (1-5) de un ítem ya publicado sin tocar el resto.
    edits_file, pubs = write_files(tmp_path, [edit(importance=5)])

    apply_editorial(edits_file, pubs)

    result = json.loads(pubs.read_text(encoding="utf-8"))["items"][0]
    assert result["importance"] == 5


def test_apply_editorial_syncs_importance_to_local_database(tmp_path):
    edits_file, pubs = write_files(tmp_path, [private_edit(importance=5)])
    db = tmp_path / "radar.sqlite3"
    with Storage(db) as storage:
        storage.save_run(extracted_private_payload())

    apply_editorial(edits_file, pubs, db)

    with Storage(db) as storage:
        exported = storage.export_payload()
    assert exported["items"][0]["importance"] == 5


def test_apply_editorial_updates_local_database(tmp_path):
    edits_file, pubs = write_files(tmp_path, [private_edit()])
    db = tmp_path / "radar.sqlite3"
    with Storage(db) as storage:
        storage.save_run(extracted_private_payload())

    apply_editorial(edits_file, pubs, db)

    with Storage(db) as storage:
        exported = storage.export_payload()
    assert exported["items"][0]["title"].startswith("SHCP actualiza")
    assert exported["items"][0]["ai_generated"] is True


@pytest.mark.parametrize(
    ("bad_edit", "message_part"),
    [
        (edit(id="dof:999"), "id inexistente"),
        (edit(summary="muy corto"), "palabras"),
        (edit(summary=" ".join(f"palabra{i}" for i in range(1001))), "palabras"),
        (edit(summary_teaser=" ".join(f"avance{i}" for i in range(71))), "teaser tiene 71"),
        (edit(card_body="## Qué se publicó\n\nSolo una sección"), "sección"),
        (edit(card_body=CARD_BODY_WITH_ACT_NUMBER), "número de acto"),
        (edit(title="Oficio 500-05-2026-1 comunica listado"), "número de oficio"),
        (edit(relevance_score=99), "no editables"),
        (edit(case_facts="   "), "vacío"),
        (edit(case_facts="## Encabezado no permitido"), "encabezados"),
        (edit(importance=6), "entre 1 y 5"),
        (edit(importance=0), "entre 1 y 5"),
        (edit(importance="5"), "entre 1 y 5"),
    ],
)
def test_apply_editorial_rejects_invalid_edits(tmp_path, bad_edit, message_part):
    edits_file, pubs = write_files(tmp_path, [bad_edit])
    original = pubs.read_text(encoding="utf-8")

    with pytest.raises(EditorialError, match=message_part):
        apply_editorial(edits_file, pubs)

    assert pubs.read_text(encoding="utf-8") == original


def test_apply_editorial_recomposes_meta_line_with_canonical_organ(tmp_path):
    # Bug #19: la línea meta de la ficha debe mostrar el órgano canónico
    # (issuing_body), no la autoridad cruda del recolector.
    payload = publications_payload()
    payload["items"][0]["authority"] = "SECRETARIA DE ECONOMIA"
    payload["items"][0]["issuing_body"] = "Secretaría de Economía"
    payload = prepare_payload(payload, force=True)
    pubs = tmp_path / "publications.json"
    pubs.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    edits_file = tmp_path / "edits.json"
    edits_file.write_text(
        json.dumps(
            {"items": [edit(content_hash=payload["items"][0]["content_hash"])]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    apply_editorial(edits_file, pubs)

    light = json.loads(pubs.read_text(encoding="utf-8"))["items"][0]
    markdown = permanent_item(pubs, light)["detail_markdown"]
    assert "**Órgano:** Secretaría de Economía" in markdown
    assert "SECRETARIA DE ECONOMIA" not in markdown


def test_apply_editorial_is_all_or_nothing(tmp_path):
    edits_file, pubs = write_files(tmp_path, [edit(), edit(id="dof:999")])
    original = pubs.read_text(encoding="utf-8")

    with pytest.raises(EditorialError):
        apply_editorial(edits_file, pubs)

    assert pubs.read_text(encoding="utf-8") == original


def test_apply_editorial_recomposes_detail_markdown_from_edited_fields(tmp_path):
    new_card_body = (
        "## Qué se publicó\n\nSHCP deja sin efectos la presunción de folios fiscales.\n\n"
        "## Sustancia\n\nSe excluyen del listado 69-B los contribuyentes que acreditaron.\n\n"
        "## Fuente\n\n[Abrir publicación oficial](https://example.gob.mx/doc)"
    )
    edits_file, pubs = write_files(tmp_path, [edit(card_body=new_card_body)])

    apply_editorial(edits_file, pubs)

    light = json.loads(pubs.read_text(encoding="utf-8"))["items"][0]
    result = permanent_item(pubs, light)
    markdown = result["detail_markdown"]
    assert markdown.startswith("# SHCP actualiza")
    assert (
        "## Qué se publicó\n\nSHCP deja sin efectos la presunción de folios fiscales." in markdown
    )
    assert (
        "## Sustancia\n\nSe excluyen del listado 69-B los contribuyentes que acreditaron."
        in markdown
    )
    # La ficha vieja del fixture ("## Resumen ejecutivo") queda reemplazada.
    assert "Resumen ejecutivo" not in markdown


def test_apply_editorial_accepts_optional_case_facts_and_recomposes_section(tmp_path):
    case_facts = "México y la demandante suscribieron un contrato de concesión en 2010."
    edits_file, pubs = write_files(tmp_path, [edit(case_facts=case_facts)])

    apply_editorial(edits_file, pubs)

    light = json.loads(pubs.read_text(encoding="utf-8"))["items"][0]
    result = permanent_item(pubs, light)
    assert result["case_facts"] == case_facts
    assert "## Hechos del asunto" in result["detail_markdown"]
    assert case_facts in result["detail_markdown"]


def test_apply_editorial_syncs_case_facts_to_local_database(tmp_path):
    case_facts = "México y la demandante suscribieron un contrato de concesión en 2010."
    edits_file, pubs = write_files(tmp_path, [private_edit(case_facts=case_facts)])
    db = tmp_path / "radar.sqlite3"
    with Storage(db) as storage:
        storage.save_run(extracted_private_payload())

    apply_editorial(edits_file, pubs, db)

    with Storage(db) as storage:
        exported = storage.export_payload()
    assert exported["items"][0]["case_facts"] == case_facts
    assert "## Hechos del asunto" in exported["items"][0]["detail_markdown"]


def test_apply_editorial_applies_valid_digest_to_payload(tmp_path):
    edits_file, pubs = write_files(tmp_path, [edit()], digest=digest_block())

    apply_editorial(edits_file, pubs)

    result = json.loads(pubs.read_text(encoding="utf-8"))
    assert result["digest"]["groups"][0]["label"] == "Fiscal"
    assert result["digest"]["groups"][0]["items"][0]["id"] == "dof:1"


@pytest.mark.parametrize(
    ("bad_digest", "message_part"),
    [
        (digest_block(id="dof:999"), "does not exist"),
        (digest_block(theme="muy corto"), "words"),
        (
            digest_block(
                theme="Oficio 500-05-2026-16021 sobre el listado correspondiente a la autoridad"
            ),
            "act number",
        ),
        ({"groups": []}, "non-empty list"),
    ],
)
def test_apply_editorial_rejects_invalid_digest_and_leaves_file_untouched(
    tmp_path, bad_digest, message_part
):
    edits_file, pubs = write_files(tmp_path, [edit()], digest=bad_digest)
    original = pubs.read_text(encoding="utf-8")

    with pytest.raises(EditorialError, match=message_part):
        apply_editorial(edits_file, pubs)

    assert pubs.read_text(encoding="utf-8") == original


def test_apply_editorial_accepts_edition_only_and_writes_light_artifact(tmp_path):
    payload = publications_payload()
    payload["items"][0]["published_at"] = "2026-07-07"
    payload["items"][0]["published_year"] = 2026
    payload["items"][0]["published_month"] = 7
    payload["items"][0]["published_day"] = 7
    pubs = tmp_path / "publications.json"
    pubs.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    edits_file = tmp_path / "edits.json"
    edits_file.write_text(
        json.dumps({"items": [], "edition": edition_block()}, ensure_ascii=False),
        encoding="utf-8",
    )

    applied = apply_editorial(edits_file, pubs)

    result = json.loads(pubs.read_text(encoding="utf-8"))
    light = json.loads((tmp_path / "edition.json").read_text(encoding="utf-8"))
    assert applied == 0
    assert result["edition"]["lead_id"] == "dof:1"
    assert light["signals"][0]["why_it_matters"].startswith("La actualización")
