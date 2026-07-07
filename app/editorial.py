from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.storage import Storage
from app.text import words
from app.validation import CARD_BODY_SECTIONS, OFFICE_NUMBER_TITLE

EDITABLE_FIELDS = ("title", "summary", "card_body")


class EditorialError(ValueError):
    """Una edición editorial inválida; nada se aplica si alguna falla."""


def apply_editorial(
    edits_path: Path,
    publications_path: Path,
    database_path: Path | None = None,
) -> int:
    """Aplica ediciones editoriales al JSON publicado (y a la última corrida
    de la base local si existe).

    Este comando es el único canal de escritura de la rutina editorial de la
    nube: solo permite reemplazar `title`, `summary` y `card_body` de ítems
    existentes (marcándolos `ai_generated=true`), con validación dura. Todo o
    nada: si una edición es inválida, no se aplica ninguna.
    """
    edits_payload = json.loads(edits_path.read_text(encoding="utf-8"))
    edits = edits_payload.get("items")
    if not isinstance(edits, list) or not edits:
        raise EditorialError("el archivo de ediciones debe traer una lista 'items' no vacía")

    payload = json.loads(publications_path.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in payload.get("items", [])}

    validated: dict[str, dict[str, str]] = {}
    for index, edit in enumerate(edits):
        edit_id = _validate_edit(index, edit, by_id)
        if edit_id in validated:
            raise EditorialError(f"items[{index}]: id duplicado en las ediciones ({edit_id})")
        validated[edit_id] = {field: edit[field].strip() for field in EDITABLE_FIELDS}

    for edit_id, fields in validated.items():
        item = by_id[edit_id]
        for field, value in fields.items():
            item[field] = value
        item["ai_generated"] = True

    publications_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if database_path is not None and Path(database_path).exists():
        _apply_to_database(Path(database_path), validated)

    return len(validated)


def _validate_edit(index: int, edit: Any, by_id: dict[str, dict[str, Any]]) -> str:
    if not isinstance(edit, dict):
        raise EditorialError(f"items[{index}] debe ser un objeto")

    unknown = set(edit) - {"id", *EDITABLE_FIELDS}
    if unknown:
        raise EditorialError(
            f"items[{index}] trae campos no editables: {', '.join(sorted(unknown))}"
        )

    edit_id = edit.get("id")
    if not isinstance(edit_id, str) or edit_id not in by_id:
        raise EditorialError(f"items[{index}]: id inexistente en las publicaciones ({edit_id!r})")

    for field in EDITABLE_FIELDS:
        value = edit.get(field)
        if not isinstance(value, str) or not value.strip():
            raise EditorialError(f"items[{index}] ({edit_id}): falta el campo '{field}'")

    summary_words = len(words(edit["summary"]))
    if summary_words != 30:
        raise EditorialError(
            f"items[{index}] ({edit_id}): el resumen tiene {summary_words} palabras, deben ser 30"
        )

    for section in CARD_BODY_SECTIONS:
        if section not in edit["card_body"]:
            raise EditorialError(
                f"items[{index}] ({edit_id}): card_body sin la sección '{section}'"
            )

    if re.match(OFFICE_NUMBER_TITLE, edit["title"].strip(), flags=re.IGNORECASE):
        raise EditorialError(
            f"items[{index}] ({edit_id}): el título editorial no debe iniciar "
            "con número de oficio/acuerdo"
        )

    return edit_id


def _apply_to_database(database_path: Path, validated: dict[str, dict[str, str]]) -> None:
    with Storage(database_path) as storage:
        for edit_id, fields in validated.items():
            storage.update_document_fields(edit_id, {**fields, "ai_generated": True})
