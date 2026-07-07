from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.text import words

REQUIRED_ITEM_FIELDS = {
    "id": str,
    "source": str,
    "url": str,
    "detail_url": str,
    "official_title": str,
    "title": str,
    "summary": str,
    "description": str,
    "detail_markdown": str,
    "card_body": str,
    "published_at": str,
    "authority": str,
    "document_type": str,
    "issuing_body": str,
    "government_branch": str,
    "jurisdiction": str,
    "country_or_org": str,
    "published_year": int,
    "published_month": int,
    "published_day": int,
    "categories": list,
    "topic_tags": list,
    "subtopic_tags": list,
    "importance": int,
    "relevance_score": int,
    "ai_generated": bool,
}

CARD_BODY_SECTIONS = ("## Qué se publicó", "## Sustancia", "## Fuente")
WHAT_PUBLISHED_SECTION = "## Qué se publicó"
SUBSTANCE_SECTION = "## Sustancia"

# Secciones de la ficha única (`detail_markdown` v2, distinta del `card_body`
# de la tarjeta: comparte "Qué se publicó"/"Sustancia" pero cierra con
# "Fuente oficial" en vez de "Fuente").
SOURCE_OFICIAL_SECTION = "## Fuente oficial"
DETAIL_MARKDOWN_SECTIONS = (WHAT_PUBLISHED_SECTION, SUBSTANCE_SECTION, SOURCE_OFICIAL_SECTION)

# Rango editorial del resumen (decisión del dueño; sustituye las 30 exactas).
SUMMARY_MIN_WORDS = 40
SUMMARY_MAX_WORDS = 80

# Rango del mini-resumen por ítem del digest agrupado (contrato v6).
DIGEST_THEME_MIN_WORDS = 8
DIGEST_THEME_MAX_WORDS = 35

# Un título editorial se rige por la temática, no por el número de acto.
OFFICE_NUMBER_TITLE = r"^(oficio|acuerdo|circular|resoluci[oó]n|expediente)\s+[A-Z0-9/.-]*\d"

# Patrón de número de acto (oficio/acuerdo/circular/expediente/clave). La sección
# "Qué se publicó" describe QUÉ se publicó sin arrastrar el número del documento.
# Se exporta para reuso en el gate editorial (`app.editorial`).
ACT_NUMBER_RE = re.compile(
    r"(?:oficio|acuerdo|circular|resoluci[oó]n|expediente|no\.|núm)\s*[:.]?\s*"
    r"[A-Z0-9][A-Z0-9/.-]*\d"
    r"|\b\d{3,}[-/][A-Z0-9/.-]*\d",
    flags=re.IGNORECASE,
)


def card_section_text(card_body: str, heading: str) -> str:
    """Devuelve el texto de una sección del `card_body` (desde su encabezado
    hasta el siguiente `## ` o el final). Cadena vacía si no existe."""
    pattern = re.compile(
        rf"{re.escape(heading)}\s*(.*?)(?=\n##\s|\Z)",
        flags=re.DOTALL,
    )
    match = pattern.search(card_body)
    return match.group(1).strip() if match else ""


def validate_digest(digest: Any, existing_ids: set[str]) -> list[str]:
    """Valida el bloque opcional `digest` (resumen del corte agrupado por
    tema). Se reutiliza tal cual desde el payload publicado y desde el gate
    editorial (`app.editorial`), que valida el mismo bloque en el archivo de
    ediciones antes de aplicarlo. `existing_ids` acota a qué ítems puede
    referirse cada entrada del digest."""
    errors: list[str] = []
    if not isinstance(digest, dict):
        errors.append("digest must be an object")
        return errors

    unknown_top = set(digest) - {"groups"}
    if unknown_top:
        errors.append(f"digest has unknown keys: {sorted(unknown_top)}")

    groups = digest.get("groups")
    if not isinstance(groups, list) or not groups:
        errors.append("digest.groups must be a non-empty list")
        return errors

    seen_ids: set[str] = set()
    for group_index, group in enumerate(groups):
        prefix = f"digest.groups[{group_index}]"
        if not isinstance(group, dict):
            errors.append(f"{prefix} must be an object")
            continue

        unknown_group = set(group) - {"label", "items"}
        if unknown_group:
            errors.append(f"{prefix} has unknown keys: {sorted(unknown_group)}")

        label = group.get("label")
        if not isinstance(label, str) or not label.strip():
            errors.append(f"{prefix}.label must be a non-empty string")

        items = group.get("items")
        if not isinstance(items, list) or not items:
            errors.append(f"{prefix}.items must be a non-empty list")
            continue

        for item_index, item in enumerate(items):
            item_prefix = f"{prefix}.items[{item_index}]"
            if not isinstance(item, dict):
                errors.append(f"{item_prefix} must be an object")
                continue

            unknown_item = set(item) - {"id", "organ", "theme"}
            if unknown_item:
                errors.append(f"{item_prefix} has unknown keys: {sorted(unknown_item)}")

            item_id = item.get("id")
            if not isinstance(item_id, str) or item_id not in existing_ids:
                errors.append(f"{item_prefix}.id does not exist in publications ({item_id!r})")
            elif item_id in seen_ids:
                errors.append(f"{item_prefix}.id is duplicated in the digest ({item_id})")
            else:
                seen_ids.add(item_id)

            organ = item.get("organ")
            if not isinstance(organ, str) or not organ.strip():
                errors.append(f"{item_prefix}.organ must be a non-empty string")

            theme = item.get("theme")
            if not isinstance(theme, str) or not theme.strip():
                errors.append(f"{item_prefix}.theme must be a non-empty string")
                continue
            theme_words = len(words(theme))
            if not DIGEST_THEME_MIN_WORDS <= theme_words <= DIGEST_THEME_MAX_WORDS:
                errors.append(
                    f"{item_prefix}.theme has {theme_words} words "
                    f"(must be {DIGEST_THEME_MIN_WORDS}-{DIGEST_THEME_MAX_WORDS})"
                )
            if ACT_NUMBER_RE.search(theme):
                errors.append(f"{item_prefix}.theme must not contain an act number")

    return errors


@dataclass(frozen=True)
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_publications_payload(payload: dict[str, Any]) -> ValidationReport:
    errors: list[str] = []
    warnings: list[str] = []
    source_errors: list[str] = []

    _validate_top_level(payload, errors)
    items = payload.get("items")
    sources = payload.get("sources")

    if isinstance(items, list):
        expected_total = payload.get("total_items")
        if isinstance(expected_total, int) and expected_total != len(items):
            errors.append(f"total_items is {expected_total}, but items has {len(items)} records")
        for index, item in enumerate(items):
            _validate_item(index, item, errors, warnings)

    digest = payload.get("digest")
    if digest is not None:
        existing_ids = {
            item["id"]
            for item in (items if isinstance(items, list) else [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        errors.extend(validate_digest(digest, existing_ids))

    if isinstance(sources, list):
        for index, source in enumerate(sources):
            _validate_source(index, source, errors, source_errors)
        if sources and len(source_errors) == len(sources):
            errors.append("all sources are in error state")
        elif source_errors:
            warnings.extend(source_errors)

    return ValidationReport(errors=errors, warnings=warnings, source_errors=source_errors)


def _validate_top_level(payload: dict[str, Any], errors: list[str]) -> None:
    if not isinstance(payload.get("generated_at"), str):
        errors.append("generated_at must be a string")
    else:
        try:
            datetime.fromisoformat(payload["generated_at"])
        except ValueError:
            errors.append("generated_at must be an ISO datetime")

    if not isinstance(payload.get("lookback_days"), int):
        errors.append("lookback_days must be an integer")
    if not isinstance(payload.get("total_items"), int):
        errors.append("total_items must be an integer")
    if not isinstance(payload.get("sources"), list):
        errors.append("sources must be a list")
    if not isinstance(payload.get("items"), list):
        errors.append("items must be a list")


def _validate_item(index: int, item: Any, errors: list[str], warnings: list[str]) -> None:
    if not isinstance(item, dict):
        errors.append(f"items[{index}] must be an object")
        return

    for field_name, field_type in REQUIRED_ITEM_FIELDS.items():
        value = item.get(field_name)
        if field_type is int and isinstance(value, bool):
            errors.append(f"items[{index}].{field_name} must be int")
            continue
        if not isinstance(value, field_type):
            errors.append(f"items[{index}].{field_name} must be {field_type.__name__}")

    for optional_field in ("case_parties", "case_status", "case_facts"):
        if optional_field in item and not isinstance(item.get(optional_field), str):
            errors.append(f"items[{index}].{optional_field} must be str")

    if isinstance(item.get("summary"), str):
        count = len(words(item["summary"]))
        if not SUMMARY_MIN_WORDS <= count <= SUMMARY_MAX_WORDS:
            errors.append(
                f"items[{index}].summary has {count} words "
                f"(must be {SUMMARY_MIN_WORDS}-{SUMMARY_MAX_WORDS})"
            )

    if isinstance(item.get("card_body"), str):
        card_body = item["card_body"]
        for section in CARD_BODY_SECTIONS:
            if section not in card_body:
                errors.append(f"items[{index}].card_body is missing section '{section}'")
        if ACT_NUMBER_RE.search(card_section_text(card_body, WHAT_PUBLISHED_SECTION)):
            errors.append(
                f"items[{index}].card_body '{WHAT_PUBLISHED_SECTION}' "
                "must not contain an act number"
            )

    if isinstance(item.get("title"), str) and re.match(
        OFFICE_NUMBER_TITLE, item["title"].strip(), flags=re.IGNORECASE
    ):
        warnings.append(
            f"items[{index}].title starts with an office/act number; "
            "editorial titles should lead with the substance"
        )

    if isinstance(item.get("jurisdiction"), str) and item["jurisdiction"] not in {
        "nacional",
        "internacional",
    }:
        errors.append(f"items[{index}].jurisdiction must be nacional or internacional")

    importance = item.get("importance")
    is_plain_int = isinstance(importance, int) and not isinstance(importance, bool)
    if is_plain_int and not 1 <= importance <= 5:
        errors.append(f"items[{index}].importance must be between 1 and 5")

    for tags_field in ("categories", "topic_tags", "subtopic_tags"):
        if isinstance(item.get(tags_field), list):
            for tag_index, tag in enumerate(item[tags_field]):
                if not isinstance(tag, str) or not tag.strip():
                    errors.append(f"items[{index}].{tags_field}[{tag_index}] must be a string")

    if isinstance(item.get("published_at"), str):
        try:
            datetime.fromisoformat(item["published_at"])
        except ValueError:
            errors.append(f"items[{index}].published_at must be an ISO date")

    if isinstance(item.get("url"), str) and not item["url"].startswith(("http://", "https://")):
        errors.append(f"items[{index}].url must be an absolute URL")

    if isinstance(item.get("detail_url"), str) and not item["detail_url"].startswith(
        "ficha.html?id="
    ):
        errors.append(f"items[{index}].detail_url must point to ficha.html")

    if isinstance(item.get("detail_markdown"), str):
        markdown = item["detail_markdown"]
        if not markdown.startswith("# "):
            errors.append(f"items[{index}].detail_markdown must start with a level 1 heading")
        # El corte publicado antes del contrato v6 trae la ficha vieja (con
        # "## Resumen ejecutivo" en vez de "## Qué se publicó"/"## Sustancia").
        # Degradar la ausencia de las secciones nuevas a advertencia evita
        # invalidar ese corte ya publicado; solo se exige como error el
        # encabezado de nivel 1, que ambas versiones cumplen. La rutina
        # editorial recompone la ficha con el builder v2 en cuanto vuelve a
        # correr sobre un ítem (`app.editorial.apply_editorial`).
        for section in DETAIL_MARKDOWN_SECTIONS:
            if section not in markdown:
                warnings.append(
                    f"items[{index}].detail_markdown is missing section '{section}' "
                    "(v6 ficha contract)"
                )


def _validate_source(
    index: int,
    source: Any,
    errors: list[str],
    source_errors: list[str],
) -> None:
    if not isinstance(source, dict):
        errors.append(f"sources[{index}] must be an object")
        return

    name = source.get("source")
    status = source.get("status")
    if not isinstance(name, str) or not name.strip():
        errors.append(f"sources[{index}].source must be a string")
        name = f"sources[{index}]"
    if status not in {"ok", "error"}:
        errors.append(f"sources[{index}].status must be ok or error")
    if not isinstance(source.get("items_found"), int):
        errors.append(f"sources[{index}].items_found must be an integer")
    attempts = source.get("attempts")
    if not isinstance(attempts, int) or attempts < 1:
        errors.append(f"sources[{index}].attempts must be a positive integer")
    if status == "error":
        message = source.get("error") or "unknown error"
        source_errors.append(f"{name}: {message}")
