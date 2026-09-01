from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.edition import (
    PublishError,
    load_manifested_items,
    prepare_payload,
    write_site_artifacts_legacy,
)
from app.markdown import build_detail_markdown
from app.storage import Storage
from app.text import words
from app.validation import (
    CARD_BODY_SECTIONS,
    CONSEQUENCE_NOUN_RE,
    GENERIC_HEADLINE_RE,
    OFFICE_NUMBER_TITLE,
    SUBSTANTIVE_HEADLINE_RE,
    SUMMARY_MAX_WORDS,
    SUMMARY_MIN_WORDS,
    TEASER_MAX_WORDS,
    TEASER_MIN_WORDS,
    WHAT_PUBLISHED_SECTION,
    card_section_text,
    contains_act_number,
    validate_digest,
    validate_edition,
    validate_publications_payload,
    validate_site_artifacts,
)

EDITABLE_FIELDS = ("title", "summary_teaser", "summary", "card_body")
STRUCTURED_EDITABLE_FIELDS = (
    "executive_summary",
    "detailed_summary",
    "impacts",
    "recommended_actions",
    "evidence",
    "coverage",
)
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

    database_mode = database_path is not None
    if database_mode:
        database_path = Path(database_path)
        if not database_path.exists():
            raise EditorialError(f"la base privada no existe: {database_path}")
        with Storage(database_path) as storage:
            payload = prepare_payload(storage.export_payload())
            private_documents = {
                item["id"]: item for item in storage.all_documents()
            }
    else:
        payload = prepare_payload(
            json.loads(publications_path.read_text(encoding="utf-8")),
        )
    current_by_id = {item["id"]: item for item in payload.get("items", [])}
    manifest_path = publications_path.with_name("manifest.json")
    if manifest_path.exists() and not database_mode:
        baseline_report = validate_site_artifacts(manifest_path)
        if not baseline_report.ok:
            raise EditorialError(
                "el corte base no pasa la validación transaccional: "
                + "; ".join(baseline_report.errors[:5])
            )
    by_id: dict[str, dict[str, Any]] = private_documents if database_mode else {}
    if not database_mode:
        try:
            by_id = load_manifested_items(publications_path)
        except PublishError as exc:
            raise EditorialError(f"no se pudieron cargar las fichas permanentes: {exc}") from exc
    # publications.json es deliberadamente ligero en v8. Para editar un ítem
    # vigente se recupera su ficha permanente completa y se vuelve a enlazar
    # al payload móvil; sólo el bootstrap sin manifiesto usa el índice.
    rich_current: list[dict[str, Any]] = []
    for light_item in payload.get("items", []):
        rich = by_id.get(light_item["id"], light_item)
        rich_current.append(rich)
    payload["items"] = rich_current
    current_by_id = {item["id"]: item for item in rich_current}
    by_id.update(current_by_id)

    validated: dict[str, dict[str, Any]] = {}
    for index, edit in enumerate(edits):
        document_ids = edit.get("document_ids") if isinstance(edit, dict) else None
        edit_for_validation = dict(edit) if isinstance(edit, dict) else edit
        if isinstance(edit_for_validation, dict):
            edit_for_validation.pop("document_ids", None)
        edit_id, fields = _validate_edit(
            index,
            edit_for_validation,
            by_id,
            require_source_hash=database_mode,
        )
        target_ids = document_ids if document_ids is not None else [edit_id]
        if (
            not isinstance(target_ids, list)
            or not target_ids
            or any(not isinstance(target, str) or target not in by_id for target in target_ids)
        ):
            raise EditorialError(f"items[{index}]: document_ids inválidos")
        source_hash = by_id[edit_id].get("source_content_hash")
        source_contract = (
            by_id[edit_id].get("extraction_method"),
            by_id[edit_id].get("source_sections"),
        )
        for target_id in target_ids:
            if by_id[target_id].get("source_content_hash") != source_hash:
                raise EditorialError(
                    f"items[{index}]: document_ids no comparten el hash de fuente"
                )
            target_contract = (
                by_id[target_id].get("extraction_method"),
                by_id[target_id].get("source_sections"),
            )
            if target_contract != source_contract:
                raise EditorialError(
                    f"items[{index}]: document_ids no comparten fuente y cobertura"
                )
            if target_id in validated:
                raise EditorialError(
                    f"items[{index}]: id duplicado en las ediciones ({target_id})"
                )
            target_fields = deepcopy(fields)
            if isinstance(target_fields.get("evidence"), dict):
                # La procedencia no se confía al agente. Se deriva del
                # registro privado para cada alias que comparte los bytes.
                target_fields["evidence"] = _canonical_evidence(
                    target_fields["evidence"], by_id[target_id]
                )
            validated[target_id] = target_fields

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
        if database_mode and (
            item.get("extraction_status") != "complete"
            or not str(item.get("extracted_text") or "").strip()
            or not str(item.get("source_content_hash") or "").strip()
        ):
            raise EditorialError(
                f"{edit_id}: la fuente completa no está extraída y ligada a su hash"
            )
        for field, value in fields.items():
            item[field] = value
        item["ai_generated"] = True
        if not database_mode:  # compatibilidad de migración para artefactos v7
            item["extraction_status"] = "complete"
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
    if database_mode:
        _apply_to_database(
            database_path,
            validated,
            {
                edit_id: str(by_id[edit_id].get("source_content_hash") or "")
                for edit_id in validated
            },
            {
                edit_id: {
                    name: by_id[edit_id].get(name)
                    for name in (
                        "extraction_status",
                        "source_revalidation_status",
                        "extraction_version",
                        "extraction_method",
                        "source_sections",
                    )
                }
                for edit_id in validated
            },
        )
    else:
        # Este canal explícito de migración acaba de validar la edición; sólo
        # aquí se permite reconciliarla con el envelope público v7 vigente.
        payload["_allow_legacy_editorial_reconciliation"] = True
        write_site_artifacts_legacy(payload, publications_path, edition_path)

    return len(validated)


def _validate_edit(
    index: int,
    edit: Any,
    by_id: dict[str, dict[str, Any]],
    *,
    require_source_hash: bool = False,
) -> tuple[str, dict[str, Any]]:
    if not isinstance(edit, dict):
        raise EditorialError(f"items[{index}] debe ser un objeto")

    unknown = set(edit) - {
        "id",
        "content_hash",
        "source_content_hash",
        *EDITABLE_FIELDS,
        *STRUCTURED_EDITABLE_FIELDS,
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

    private_source_hash = by_id[edit_id].get("source_content_hash")
    expected_hash = (
        edit.get("source_content_hash")
        if require_source_hash
        else edit.get("source_content_hash") or edit.get("content_hash")
    )
    if not isinstance(expected_hash, str) or not expected_hash:
        raise EditorialError(
            f"items[{index}] ({edit_id}): falta el hash de la fuente revisada"
        )
    accepted_hashes = (
        {str(private_source_hash)}
        if require_source_hash and private_source_hash
        else {
            str(value)
            for value in (private_source_hash, by_id[edit_id].get("content_hash"))
            if value
        }
    )
    if expected_hash not in accepted_hashes:
        raise EditorialError(
            f"items[{index}] ({edit_id}): el hash de la fuente cambió; "
            "vuelve a revisar la evidencia"
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
    if not TEASER_MIN_WORDS <= teaser_words <= TEASER_MAX_WORDS:
        raise EditorialError(
            f"items[{index}] ({edit_id}): el teaser tiene {teaser_words} palabras, "
            f"debe tener {TEASER_MIN_WORDS}-{TEASER_MAX_WORDS}"
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

    title_word_count = len(words(fields["title"]))
    if not 5 <= title_word_count <= 18 or len(fields["title"]) > 120:
        raise EditorialError(
            f"items[{index}] ({edit_id}): el título debe tener 5-18 palabras "
            "y un máximo de 120 caracteres"
        )
    official_title = str(by_id[edit_id].get("official_title") or "")
    if _normalized_similarity(fields["title"], official_title) >= 0.8:
        raise EditorialError(
            f"items[{index}] ({edit_id}): el título editorial no debe copiar el título oficial"
        )
    if GENERIC_HEADLINE_RE.search(fields["title"]) or not (
        SUBSTANTIVE_HEADLINE_RE.search(fields["title"])
        or CONSEQUENCE_NOUN_RE.search(fields["title"])
    ):
        raise EditorialError(
            f"items[{index}] ({edit_id}): el título debe expresar una consecuencia, "
            "decisión o cambio verificable"
        )
    if by_id[edit_id].get("source_id") == "5795726":
        normalized = fields["title"].casefold()
        if not (
            "cfdi" in normalized
            and ("falso" in normalized or "falsos" in normalized)
            and re.search(r"(?:30|treinta)\s+días|un\s+mes", normalized)
            and re.search(r"revert|dejar\s+sin\s+efecto|efectos?", normalized)
        ):
            raise EditorialError(
                f"items[{index}] ({edit_id}): el titular debe comunicar CFDI falsos, "
                "30 días y la reversión de sus efectos"
            )

    structured_present = any(field in edit for field in STRUCTURED_EDITABLE_FIELDS)
    require_structured = any(
        field in by_id[edit_id]
        for field in ("extracted_text", "source_content_hash", "extraction_method")
    )
    if require_structured and not structured_present:
        raise EditorialError(
            f"items[{index}] ({edit_id}): la editorial v8 requiere los cuatro bloques estructurados"
        )
    if structured_present:
        missing = [field for field in STRUCTURED_EDITABLE_FIELDS if field not in edit]
        if missing:
            raise EditorialError(
                f"items[{index}] ({edit_id}): faltan campos estructurados: "
                + ", ".join(missing)
            )
        _validate_structured_edit(index, edit_id, edit, by_id[edit_id])
        for field in STRUCTURED_EDITABLE_FIELDS:
            fields[field] = edit[field]

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


def _apply_to_database(
    database_path: Path,
    validated: dict[str, dict[str, Any]],
    expected_source_hashes: dict[str, str],
    expected_extraction_contracts: dict[str, dict[str, Any]],
) -> None:
    with Storage(database_path) as storage:
        storage.update_document_fields_atomic(
            {
                edit_id: {
                    **fields,
                    "ai_generated": True,
                    "editorial_status": "complete",
                    "review_reason": None,
                }
                for edit_id, fields in validated.items()
            },
            expected_source_hashes=expected_source_hashes,
            expected_extraction_contracts=expected_extraction_contracts,
        )


def _normalized_similarity(left: str, right: str) -> float:
    left_tokens = set(token.casefold() for token in words(left))
    right_tokens = set(token.casefold() for token in words(right))
    return len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))


def _paragraphs(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(paragraph, str) and paragraph.strip() for paragraph in value
    )


def _evidence_refs(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(reference, str) and reference.strip() for reference in value
    )


def _validate_structured_edit(
    index: int,
    edit_id: str,
    edit: dict[str, Any],
    source_item: dict[str, Any],
) -> None:
    prefix = f"items[{index}] ({edit_id})"
    executive = edit["executive_summary"]
    if not _paragraphs(executive) or len(executive) > 4:
        raise EditorialError(f"{prefix}: executive_summary debe contener 1-4 párrafos")
    source_words = len(words(str(source_item.get("extracted_text") or "")))
    source_inventory = source_item.get("source_sections")
    exceptionally_brief = (
        source_words <= 180
        and isinstance(source_inventory, list)
        and len(source_inventory) == 1
    )
    if len(executive) == 1 and not exceptionally_brief:
        raise EditorialError(
            f"{prefix}: executive_summary requiere 2-4 párrafos salvo fuente breve"
        )
    if len(words(" ".join(executive))) > 220:
        raise EditorialError(f"{prefix}: executive_summary excede 220 palabras")

    detailed = edit["detailed_summary"]
    if not isinstance(detailed, list) or not detailed:
        raise EditorialError(f"{prefix}: detailed_summary debe contener secciones")
    for section in detailed:
        if not isinstance(section, dict) or set(section) != {
            "heading",
            "paragraphs",
            "evidence_refs",
        }:
            raise EditorialError(f"{prefix}: sección detallada inválida")
        if not isinstance(section["heading"], str) or not section["heading"].strip():
            raise EditorialError(f"{prefix}: heading detallado vacío")
        if not _paragraphs(section["paragraphs"]) or not _evidence_refs(section["evidence_refs"]):
            raise EditorialError(f"{prefix}: párrafos o evidencia detallada inválidos")

    impacts = edit["impacts"]
    if not isinstance(impacts, dict) or set(impacts) != {"general", "sectors"}:
        raise EditorialError(f"{prefix}: impacts debe separar general y sectors")
    general = impacts["general"]
    if (
        not isinstance(general, dict)
        or set(general) != {"paragraphs", "evidence_refs"}
        or not _paragraphs(general.get("paragraphs"))
        or not _evidence_refs(general.get("evidence_refs"))
    ):
        raise EditorialError(f"{prefix}: impacto general inválido")
    sectors = impacts["sectors"]
    if not isinstance(sectors, list) or not sectors:
        raise EditorialError(f"{prefix}: debe nombrarse al menos un sector")
    for sector in sectors:
        if (
            not isinstance(sector, dict)
            or set(sector) != {"sector", "paragraphs", "evidence_refs"}
            or not isinstance(sector.get("sector"), str)
        ):
            raise EditorialError(f"{prefix}: sector inválido")
        if (
            not sector["sector"].strip()
            or not _paragraphs(sector.get("paragraphs"))
            or not _evidence_refs(sector.get("evidence_refs"))
        ):
            raise EditorialError(f"{prefix}: impacto sectorial incompleto")

    actions = edit["recommended_actions"]
    if not isinstance(actions, list) or not actions:
        raise EditorialError(f"{prefix}: recommended_actions debe identificar afectados")
    action_keys = {
        "affected_group",
        "paragraphs",
        "trigger",
        "deadline",
        "legal_basis",
        "evidence_refs",
    }
    for action in actions:
        if not isinstance(action, dict) or set(action) != action_keys:
            raise EditorialError(f"{prefix}: acción recomendada inválida")
        if not isinstance(action["affected_group"], str) or not action["affected_group"].strip():
            raise EditorialError(f"{prefix}: affected_group vacío")
        if not _paragraphs(action["paragraphs"]) or not _evidence_refs(action["evidence_refs"]):
            raise EditorialError(f"{prefix}: acción sin prosa o evidencia")
        if any(
            action[key] is not None and not isinstance(action[key], str)
            for key in ("trigger", "deadline", "legal_basis")
        ):
            raise EditorialError(f"{prefix}: detonante, plazo o fundamento inválidos")

    evidence = edit["evidence"]
    required_evidence = {
        "official_url", "retrieved_at", "content_hash", "extraction_method", "locators"
    }
    if not isinstance(evidence, dict) or set(evidence) != required_evidence:
        raise EditorialError(f"{prefix}: evidence está incompleta")
    if not isinstance(evidence.get("official_url"), str) or not evidence["official_url"].startswith("https://"):
        raise EditorialError(f"{prefix}: evidence.official_url debe ser HTTPS")
    if evidence["official_url"] not in {
        source_item.get("url"), source_item.get("canonical_url")
    }:
        raise EditorialError(f"{prefix}: evidence.official_url no coincide con la fuente")
    if evidence.get("content_hash") != source_item.get("source_content_hash"):
        raise EditorialError(f"{prefix}: evidence.content_hash no coincide con la extracción")
    if evidence.get("extraction_method") != source_item.get("extraction_method"):
        raise EditorialError(f"{prefix}: evidence.extraction_method no coincide")
    if not isinstance(evidence.get("retrieved_at"), str):
        raise EditorialError(f"{prefix}: evidence.retrieved_at debe ser ISO")
    if evidence.get("retrieved_at") != source_item.get("extraction_retrieved_at"):
        raise EditorialError(
            f"{prefix}: evidence.retrieved_at no coincide con la extracción privada"
        )
    locators = evidence.get("locators")
    if not isinstance(locators, list) or not locators:
        raise EditorialError(f"{prefix}: evidence debe incluir localizadores")
    locator_ids = {
        locator.get("id")
        for locator in locators
        if isinstance(locator, dict) and isinstance(locator.get("id"), str)
    }
    if len(locator_ids) != len(locators):
        raise EditorialError(f"{prefix}: localizadores de evidencia inválidos")
    inventory = source_item.get("source_sections")
    inventory_ids = {
        row.get("id")
        for row in inventory
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    } if isinstance(inventory, list) else set()
    if not inventory_ids or locator_ids != inventory_ids:
        raise EditorialError(
            f"{prefix}: evidence.locators no coincide con el inventario extraído"
        )

    referenced: set[str] = set()
    for section in detailed:
        referenced.update(section["evidence_refs"])
    referenced.update(general["evidence_refs"])
    for sector in sectors:
        referenced.update(sector["evidence_refs"])
    for action in actions:
        referenced.update(action["evidence_refs"])
    if not referenced.issubset(locator_ids):
        raise EditorialError(f"{prefix}: evidence_refs contiene localizadores inexistentes")

    coverage_targets = {
        "executive_summary",
        "detailed_summary",
        "impacts.general",
        "impacts.sectors",
        "recommended_actions",
    }
    coverage = edit["coverage"]
    if not isinstance(coverage, list) or not coverage:
        raise EditorialError(f"{prefix}: coverage debe cubrir la fuente completa")
    for row in coverage:
        if (
            not isinstance(row, dict)
            or set(row) != {
                "source_ref", "disposition", "target_sections", "reason"
            }
            or row.get("source_ref") not in locator_ids
        ):
            raise EditorialError(f"{prefix}: coverage contiene una referencia inexistente")
        if row.get("disposition") not in {"summarized", "not_material"}:
            raise EditorialError(f"{prefix}: disposición de cobertura inválida")
        targets = row.get("target_sections")
        reason = row.get("reason")
        if row.get("disposition") == "summarized" and (
            not isinstance(targets, list)
            or not targets
            or any(target not in coverage_targets for target in targets)
            or (reason is not None and not isinstance(reason, str))
        ):
            raise EditorialError(
                f"{prefix}: cobertura resumida requiere secciones destino válidas"
            )
        if row.get("disposition") == "not_material" and (
            targets != [] or not isinstance(reason, str) or not reason.strip()
        ):
            raise EditorialError(
                f"{prefix}: una exclusión requiere lista destino vacía y justificación"
            )
    covered_ids = {
        row.get("source_ref") for row in coverage if isinstance(row, dict)
    }
    if covered_ids != inventory_ids:
        raise EditorialError(f"{prefix}: coverage deja huecos en la fuente")


def _canonical_evidence(
    supplied: dict[str, Any], source_item: dict[str, Any]
) -> dict[str, Any]:
    """Proyecta procedencia exclusivamente desde la extracción privada."""
    inventory = source_item.get("source_sections")
    locators: list[dict[str, str]] = []
    if isinstance(inventory, list):
        for row in inventory:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            label = str(row.get("label") or row["id"])
            start = row.get("start")
            end = row.get("end")
            if isinstance(start, int) and isinstance(end, int):
                location = f"caracteres {start}-{end}"
            else:
                location = label
            locators.append(
                {"id": str(row["id"]), "label": label, "location": location}
            )
    return {
        "official_url": str(
            source_item.get("canonical_url") or source_item.get("url") or ""
        ),
        "retrieved_at": str(source_item.get("extraction_retrieved_at") or ""),
        "content_hash": str(source_item.get("source_content_hash") or ""),
        "extraction_method": str(source_item.get("extraction_method") or ""),
        "locators": locators,
    }
