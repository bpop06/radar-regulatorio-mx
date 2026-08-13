from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.edition import PublishError, load_manifested_items, prepare_payload, write_site_artifacts
from app.markdown import build_detail_markdown
from app.storage import Storage
from app.text import ACT_NUMBER_RE, words
from app.validation import (
    CARD_BODY_SECTIONS,
    OFFICE_NUMBER_TITLE,
    SUMMARY_MAX_WORDS,
    SUMMARY_MIN_WORDS,
    TEASER_MAX_WORDS,
    WHAT_PUBLISHED_SECTION,
    card_section_text,
    contains_act_number,
    validate_digest,
    validate_edition,
    validate_publications_payload,
    validate_site_artifacts,
)

EDITABLE_FIELDS = ("title", "summary_teaser", "summary", "card_body")
# Campos sustantivos de casos que Codex puede completar sólo después de leer
# evidencia oficial. El content_hash obligatorio impide aplicar ese análisis
# sobre una versión de la fuente que ya cambió.
OPTIONAL_EDITABLE_FIELDS = (
    "case_facts",
    "case_claim",
    "case_outcome",
    "case_reasoning",
    "case_amount",
)
# importance es la curaduría editorial de qué tan relevante es un ítem ya
# publicado (bug #15): opcional igual que case_facts, pero de tipo int
# (1-5, mismo rango que exige app.validation), no texto.
OPTIONAL_EDITABLE_INT_FIELDS = ("importance",)

# case_facts es prosa de párrafos; si trajera sus propios encabezados
# rompería la jerarquía fija de la ficha única (la sección "## Hechos del
# asunto" la abre el builder, no la editorial).
_MARKDOWN_HEADING_RE = re.compile(r"(?m)^#{1,6}\s")


class EditorialError(ValueError):
    """Una edición editorial inválida; nada se aplica si alguna falla."""


def apply_editorial(
    edits_path: Path,
    publications_path: Path,
    database_path: Path | None = None,
    edition_path: Path | None = None,
) -> int:
    """Aplica ediciones editoriales al JSON publicado (y a la última corrida
    de la base local si existe).

    Este comando es el único canal de escritura de la rutina editorial de
    Codex: permite reemplazar `title`, `summary_teaser`, `summary` y
    `card_body` (obligatorios),
    análisis de caso opcional e `importance` (opcional, curaduría de qué tan
    relevante es el ítem, entero 1-5) de ítems existentes —incluidas fichas
    permanentes que ya salieron de la ventana móvil—, marcándolos
    `ai_generated=true`, más el bloque opcional `digest` del corte completo,
    con validación dura. Todo o nada: si una edición o el digest son inválidos,
    no se aplica nada.
    """
    edits_payload = json.loads(edits_path.read_text(encoding="utf-8"))
    edits = edits_payload.get("items", [])
    if not isinstance(edits, list):
        raise EditorialError("el archivo de ediciones debe traer una lista 'items'")
    if not edits and edits_payload.get("digest") is None and edits_payload.get("edition") is None:
        raise EditorialError("el archivo de ediciones no contiene cambios")

    payload = prepare_payload(
        json.loads(publications_path.read_text(encoding="utf-8")),
    )
    current_by_id = {item["id"]: item for item in payload.get("items", [])}
    manifest_path = publications_path.with_name("manifest.json")
    if manifest_path.exists():
        baseline_report = validate_site_artifacts(manifest_path)
        if not baseline_report.ok:
            raise EditorialError(
                "el corte base no pasa la validación transaccional: "
                + "; ".join(baseline_report.errors[:5])
            )
    try:
        by_id = load_manifested_items(publications_path)
    except PublishError as exc:
        raise EditorialError(f"no se pudieron cargar las fichas permanentes: {exc}") from exc
    # La versión de publications.json tiene precedencia para los ítems del
    # corte móvil y comparte identidad de objeto con payload, de modo que sus
    # ediciones se reflejan en el artefacto compatible sin una segunda copia.
    by_id.update(current_by_id)

    validated: dict[str, dict[str, Any]] = {}
    for index, edit in enumerate(edits):
        edit_id, fields = _validate_edit(index, edit, by_id)
        if edit_id in validated:
            raise EditorialError(f"items[{index}]: id duplicado en las ediciones ({edit_id})")
        validated[edit_id] = fields

    # El digest es global al corte (no por ítem): se valida junto con las
    # ediciones, ANTES de tocar el payload, para que el todo-o-nada cubra
    # también el archivo completo de ediciones.
    digest = edits_payload.get("digest")
    if digest is not None:
        digest_errors = validate_digest(digest, set(current_by_id))
        if digest_errors:
            raise EditorialError("digest inválido: " + "; ".join(digest_errors[:5]))

    edition = edits_payload.get("edition")
    if edition is not None:
        edition_errors = validate_edition(
            edition,
            list(current_by_id.values()),
            payload.get("sources", []),
        )
        if edition_errors:
            raise EditorialError("edición inválida: " + "; ".join(edition_errors[:5]))

    for edit_id, fields in validated.items():
        item = by_id[edit_id]
        for field, value in fields.items():
            item[field] = value
        item["ai_generated"] = True
        item["editorial_status"] = "complete"
        item["review_reason"] = None
        # La ficha única debe quedar coherente con la edición aplicada: se
        # recompone con el builder v2 a partir de los campos ya actualizados
        # del ítem. El pipeline la vuelve a generar igual en la próxima
        # corrida, así que no hay conflicto de fuente de verdad.
        item["detail_markdown"] = build_detail_markdown(
            title=item["title"],
            summary=item["summary"],
            official_title=item["official_title"],
            source=item["source"],
            # Línea meta y fallback muestran el órgano canónico ya resuelto
            # por app.taxonomy, no la autoridad cruda (bug #19).
            authority=item.get("issuing_body") or item["authority"],
            document_type=item.get("document_type", ""),
            published_at=item["published_at"],
            categories=tuple(item.get("categories", [])),
            source_url=item["url"],
            card_body=item["card_body"],
            case_facts=item.get("case_facts", ""),
            case_status=item.get("case_status", ""),
            case_parties=item.get("case_parties", ""),
        )
        # Se sincroniza a la base local igual que los demás campos editados.
        fields["detail_markdown"] = item["detail_markdown"]
        fields["editorial_status"] = "complete"
        fields["review_reason"] = None

    if digest is not None:
        payload["digest"] = digest
    if edition is not None:
        payload["edition"] = edition

    historical_edits = [
        by_id[edit_id] for edit_id in validated if edit_id not in current_by_id
    ]
    if historical_edits:
        # Transporte privado ya soportado por el writer: regenera ficha, nota
        # e índice mensual sin convertir el registro histórico en novedad del
        # corte actual ni incorporarlo a publications.json/edition.json.
        payload["_historical_items"] = historical_edits

    # Validar el payload fusionado EN MEMORIA antes de escribir: un fallo aquí
    # no debe dejar el archivo publicado a medio modificar.
    report = validate_publications_payload(payload)
    if not report.ok:
        raise EditorialError(
            "el payload resultante no pasa el contrato: " + "; ".join(report.errors[:5])
        )

    if edition is not None:
        # El writer reconstruye por defecto la selección tras reconciliar
        # editoriales. Sólo una edición explícitamente incluida por Codex
        # debe conservar rangos y razones personalizados.
        payload["_preserve_edition"] = True
    write_site_artifacts(payload, publications_path, edition_path)

    if database_path is not None and Path(database_path).exists():
        _apply_to_database(Path(database_path), validated)

    return len(validated)


def _validate_edit(
    index: int, edit: Any, by_id: dict[str, dict[str, Any]]
) -> tuple[str, dict[str, Any]]:
    if not isinstance(edit, dict):
        raise EditorialError(f"items[{index}] debe ser un objeto")

    unknown = set(edit) - {
        "id",
        "content_hash",
        *EDITABLE_FIELDS,
        *OPTIONAL_EDITABLE_FIELDS,
        *OPTIONAL_EDITABLE_INT_FIELDS,
    }
    if unknown:
        raise EditorialError(
            f"items[{index}] trae campos no editables: {', '.join(sorted(unknown))}"
        )

    edit_id = edit.get("id")
    if not isinstance(edit_id, str) or edit_id not in by_id:
        raise EditorialError(f"items[{index}]: id inexistente en las publicaciones ({edit_id!r})")

    expected_hash = edit.get("content_hash")
    if not isinstance(expected_hash, str) or not expected_hash:
        raise EditorialError(
            f"items[{index}] ({edit_id}): falta content_hash de la evidencia revisada"
        )
    if expected_hash != by_id[edit_id].get("content_hash"):
        raise EditorialError(
            f"items[{index}] ({edit_id}): content_hash cambió; vuelve a revisar la evidencia"
        )

    fields: dict[str, Any] = {}
    for field in EDITABLE_FIELDS:
        value = edit.get(field)
        if not isinstance(value, str) or not value.strip():
            raise EditorialError(f"items[{index}] ({edit_id}): falta el campo '{field}'")
        fields[field] = value.strip()

    summary_words = len(words(fields["summary"]))
    if not SUMMARY_MIN_WORDS <= summary_words <= SUMMARY_MAX_WORDS:
        raise EditorialError(
            f"items[{index}] ({edit_id}): el resumen tiene {summary_words} palabras, "
            f"deben ser {SUMMARY_MIN_WORDS}-{SUMMARY_MAX_WORDS}"
        )

    teaser_words = len(words(fields["summary_teaser"]))
    if teaser_words > TEASER_MAX_WORDS:
        raise EditorialError(
            f"items[{index}] ({edit_id}): el teaser tiene {teaser_words} palabras, "
            f"el máximo es {TEASER_MAX_WORDS}"
        )

    for section in CARD_BODY_SECTIONS:
        if section not in fields["card_body"]:
            raise EditorialError(
                f"items[{index}] ({edit_id}): card_body sin la sección '{section}'"
            )

    what_published = card_section_text(fields["card_body"], WHAT_PUBLISHED_SECTION)
    if contains_act_number(what_published):
        raise EditorialError(
            f"items[{index}] ({edit_id}): la sección '{WHAT_PUBLISHED_SECTION}' "
            "no debe traer número de acto"
        )

    if re.match(OFFICE_NUMBER_TITLE, fields["title"], flags=re.IGNORECASE):
        raise EditorialError(
            f"items[{index}] ({edit_id}): el título editorial no debe iniciar "
            "con número de oficio/acuerdo"
        )

    for field in OPTIONAL_EDITABLE_FIELDS:
        if field not in edit:
            continue
        value = edit[field]
        if not isinstance(value, str) or not value.strip():
            raise EditorialError(
                f"items[{index}] ({edit_id}): '{field}' no puede quedar vacío si se incluye"
            )
        if _MARKDOWN_HEADING_RE.search(value):
            raise EditorialError(
                f"items[{index}] ({edit_id}): '{field}' no debe traer encabezados markdown"
            )
        fields[field] = value.strip()

    for field in OPTIONAL_EDITABLE_INT_FIELDS:
        if field not in edit:
            continue
        value = edit[field]
        is_plain_int = isinstance(value, int) and not isinstance(value, bool)
        if not is_plain_int or not 1 <= value <= 5:
            raise EditorialError(
                f"items[{index}] ({edit_id}): '{field}' debe ser un entero entre 1 y 5"
            )
        fields[field] = value

    return edit_id, fields


def _apply_to_database(database_path: Path, validated: dict[str, dict[str, Any]]) -> None:
    with Storage(database_path) as storage:
        for edit_id, fields in validated.items():
            storage.update_document_fields(edit_id, {**fields, "ai_generated": True})
