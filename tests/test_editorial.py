import json

import pytest

from app.editorial import EditorialError, apply_editorial
from app.storage import Storage

VALID_SUMMARY = " ".join(f"palabra{i}" for i in range(45))
VALID_CARD_BODY = (
    "## Qué se publicó\n\nAcuerdo de la autoridad.\n\n"
    "## Sustancia\n\nCambio sustantivo concreto.\n\n"
    "## Fuente\n\n[Abrir publicación oficial](https://example.gob.mx/doc)"
)
CARD_BODY_WITH_ACT_NUMBER = (
    "## Qué se publicó\n\nOficio 500-05-2026-16021 de la autoridad.\n\n"
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
        "summary": VALID_SUMMARY,
        "card_body": VALID_CARD_BODY,
    }
    base.update(overrides)
    return base


def digest_block(**item_overrides) -> dict:
    item = {"id": "dof:1", "organ": "SHCP", "theme": DIGEST_THEME}
    item.update(item_overrides)
    return {"groups": [{"label": "Fiscal", "items": [item]}]}


def write_files(tmp_path, edits, digest=None):
    pubs = tmp_path / "publications.json"
    pubs.write_text(json.dumps(publications_payload(), ensure_ascii=False), encoding="utf-8")
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
    result = json.loads(pubs.read_text(encoding="utf-8"))["items"][0]
    assert result["title"].startswith("SHCP actualiza")
    assert result["ai_generated"] is True
    assert result["published_at"] == "2026-07-03"


def test_apply_editorial_updates_local_database(tmp_path):
    edits_file, pubs = write_files(tmp_path, [edit()])
    db = tmp_path / "radar.sqlite3"
    with Storage(db) as storage:
        storage.save_run(publications_payload())

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
        (edit(summary=" ".join(f"palabra{i}" for i in range(90))), "palabras"),
        (edit(card_body="## Qué se publicó\n\nSolo una sección"), "sección"),
        (edit(card_body=CARD_BODY_WITH_ACT_NUMBER), "número de acto"),
        (edit(title="Oficio 500-05-2026-1 comunica listado"), "número de oficio"),
        (edit(relevance_score=99), "no editables"),
        (edit(case_facts="   "), "vacío"),
        (edit(case_facts="## Encabezado no permitido"), "encabezados"),
    ],
)
def test_apply_editorial_rejects_invalid_edits(tmp_path, bad_edit, message_part):
    edits_file, pubs = write_files(tmp_path, [bad_edit])
    original = pubs.read_text(encoding="utf-8")

    with pytest.raises(EditorialError, match=message_part):
        apply_editorial(edits_file, pubs)

    assert pubs.read_text(encoding="utf-8") == original


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

    result = json.loads(pubs.read_text(encoding="utf-8"))["items"][0]
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

    result = json.loads(pubs.read_text(encoding="utf-8"))["items"][0]
    assert result["case_facts"] == case_facts
    assert "## Hechos del asunto" in result["detail_markdown"]
    assert case_facts in result["detail_markdown"]


def test_apply_editorial_syncs_case_facts_to_local_database(tmp_path):
    case_facts = "México y la demandante suscribieron un contrato de concesión en 2010."
    edits_file, pubs = write_files(tmp_path, [edit(case_facts=case_facts)])
    db = tmp_path / "radar.sqlite3"
    with Storage(db) as storage:
        storage.save_run(publications_payload())

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
