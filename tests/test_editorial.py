import json

import pytest

from app.editorial import EditorialError, apply_editorial
from app.storage import Storage

VALID_SUMMARY = " ".join(f"palabra{i}" for i in range(30))
VALID_CARD_BODY = (
    "## Qué se publicó\n\nAcuerdo de la autoridad.\n\n"
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
                "title": "Título extractivo original",
                "summary": VALID_SUMMARY,
                "card_body": VALID_CARD_BODY,
                "ai_generated": False,
                "published_at": "2026-07-03",
            }
        ],
    }


def edit(**overrides) -> dict:
    base = {
        "id": "dof:1",
        "title": "SHCP actualiza el listado de contribuyentes con presunción fiscal",
        "summary": VALID_SUMMARY,
        "card_body": VALID_CARD_BODY,
    }
    base.update(overrides)
    return base


def write_files(tmp_path, edits):
    pubs = tmp_path / "publications.json"
    pubs.write_text(json.dumps(publications_payload(), ensure_ascii=False), encoding="utf-8")
    edits_file = tmp_path / "edits.json"
    edits_file.write_text(json.dumps({"items": edits}, ensure_ascii=False), encoding="utf-8")
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
        (edit(card_body="## Qué se publicó\n\nSolo una sección"), "sección"),
        (edit(title="Oficio 500-05-2026-1 comunica listado"), "número de oficio"),
        (edit(relevance_score=99), "no editables"),
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
