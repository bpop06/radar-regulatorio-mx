from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.edition import (
    EDITION_KEYS,
    MAX_SIGNALS,
    PUBLIC_INDEX_FIELDS,
    PUBLIC_PERMANENT_FIELDS,
    SCHEMA_VERSION,
    SIGNAL_REFERENCE_KEYS,
    STATIC_ASSET_VERSION,
    SUMMARY_MAX_WORDS,
    SUMMARY_MIN_WORDS,
    WHY_MAX_WORDS,
    WHY_MIN_WORDS,
    build_edition_artifact,
    compute_cut_id,
    detection_date,
    item_key,
)
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

# Contrato legado, sólo para leer cortes v7 durante la migración.
LEGACY_SUMMARY_MIN_WORDS = 40
LEGACY_SUMMARY_MAX_WORDS = 80

V8_REQUIRED_ITEM_FIELDS = {
    "id": str,
    "source_id": str,
    "source": str,
    "url": str,
    "canonical_url": str,
    "detail_url": str,
    "official_title": str,
    "description": str,
    "published_at": str,
    "official_published_at": str,
    "detected_at": str,
    "first_seen_at": str,
    "last_seen_at": str,
    "content_hash": str,
    "extraction_status": str,
    "editorial_status": str,
    "detail_data_url": str,
    "official_identifiers": dict,
    "official_evidence": dict,
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

# El titular se usa tanto en el índice ligero como en la ficha. Estas reglas
# se aplican al contrato editorial estructurado; las fichas v7 leídas durante
# la migración siguen pasando por el camino legado.
TITLE_MIN_WORDS = 5
TITLE_MAX_WORDS = 18
TITLE_MAX_CHARACTERS = 120

EDITORIAL_STATUSES = {"pending", "needs_review", "complete"}
EXTRACTION_STATUSES = {
    "pending", "complete", "needs_ocr", "partial", "unsupported", "failed"
}

DETAIL_FIELDS = (
    "executive_summary",
    "detailed_summary",
    "impacts",
    "recommended_actions",
    "evidence",
    "coverage",
)

# Rango del mini-resumen por ítem del digest agrupado (contrato v6).
DIGEST_THEME_MIN_WORDS = 8
DIGEST_THEME_MAX_WORDS = 35

# Un título editorial se rige por la temática, no por el número de acto.
OFFICE_NUMBER_TITLE = (
    r"^(?:(?:oficio|acuerdo|circular|resoluci[oó]n|expediente)\s+[A-Z0-9/.-]*\d|"
    r"\d{2,}(?:[-/.]\d{2,}){2,})"
)
GENERIC_HEADLINE_RE = re.compile(
    r"\b(?:publica|emite|anuncia|informa|presenta|difunde|da a conocer)\b"
    r".{0,55}\b(?:nuevo|nueva|acuerdo|resoluci[oó]n|aviso|documento|"
    r"disposici[oó]n|medida|lineamientos|listado)\b",
    flags=re.IGNORECASE,
)
SUBSTANTIVE_HEADLINE_RE = re.compile(
    r"\b(?:modifica|elimina|crea|ampl[ií]a|reduce|exige|obliga|proh[ií]be|"
    r"autoriza|suspende|revoca|inhabilita|concede|fija|extiende|incorpora|aclara|"
    r"da|"
    r"determina|impone|confirma|invalida|ordena|restringe|permite|cambia|"
    r"establece|reemplaza|abre|cierra|actualiza|reconoce|declara)\b",
    flags=re.IGNORECASE,
)
CONSEQUENCE_NOUN_RE = re.compile(
    r"\b(?:plazo|multa|arancel|tasa|obligaci[oó]n|prohibici[oó]n|requisito|"
    r"vigencia|efecto|derecho|exenci[oó]n|sanci[oó]n|cuota)\b",
    flags=re.IGNORECASE,
)

# Patrón de número de acto (oficio/acuerdo/circular/expediente/clave). La sección
# "Qué se publicó" describe QUÉ se publicó sin arrastrar el número del documento.
# Se exporta para reuso en el gate editorial (`app.editorial`).
ACT_NUMBER_RE = re.compile(
    r"(?:oficio|acuerdo|circular|resoluci[oó]n|expediente|no\.|núm)\s*[:.]?\s*"
    r"[A-Z0-9][A-Z0-9/.-]*\d"
    r"|\b\d{3,}[-/][A-Z0-9/.-]*\d",
    flags=re.IGNORECASE,
)
YEAR_RANGE_RE = re.compile(r"\b(?:19|20)\d{2}\s*[-–—/]\s*(?:19|20)\d{2}\b")


def contains_act_number(value: str) -> bool:
    """Distingue números de acto de rangos de años legítimos (2026-2027)."""
    return bool(ACT_NUMBER_RE.search(YEAR_RANGE_RE.sub("", value)))


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
            if contains_act_number(theme):
                errors.append(f"{item_prefix}.theme must not contain an act number")

    return errors


def validate_edition(
    edition: Any,
    items: list[Any],
    sources: list[Any],
) -> list[str]:
    """Valida la selección finita de la portada y su relación con el corte."""
    errors: list[str] = []
    if not isinstance(edition, dict):
        return ["edition must be an object"]

    unknown = set(edition) - EDITION_KEYS
    if unknown:
        errors.append(f"edition has unknown keys: {sorted(unknown)}")

    edition_date = edition.get("edition_date")
    parsed_date = None
    if not isinstance(edition_date, str):
        errors.append("edition.edition_date must be an ISO date")
    else:
        try:
            parsed_date = datetime.fromisoformat(edition_date).date()
        except ValueError:
            errors.append("edition.edition_date must be an ISO date")

    state = edition.get("state")
    if state not in {"ready", "empty"}:
        errors.append("edition.state must be ready or empty")

    item_records = [item for item in items if isinstance(item, dict)]
    by_id = {
        item.get("id"): item
        for item in item_records
        if isinstance(item.get("id"), str)
    }
    today_items = (
        [
            item
            for item in item_records
            if detection_date(item) == edition_date
        ]
        if isinstance(edition_date, str)
        else []
    )
    total_today = edition.get("total_today")
    if not isinstance(total_today, int) or isinstance(total_today, bool):
        errors.append("edition.total_today must be an integer")
    elif total_today != len(today_items):
        errors.append(
            f"edition.total_today is {total_today}, but {len(today_items)} items match the date"
        )

    available_dates = sorted(
        {
            item.get("official_published_at") or item.get("published_at")
            for item in item_records
            if isinstance(item.get("official_published_at") or item.get("published_at"), str)
        },
        reverse=True,
    )
    last_available = edition.get("last_available_date")
    expected_last = available_dates[0] if available_dates else None
    if last_available != expected_last:
        errors.append(
            "edition.last_available_date must match the latest publication date "
            f"({expected_last!r})"
        )

    errors.extend(_validate_coverage(edition.get("coverage"), sources))

    signals = edition.get("signals")
    if not isinstance(signals, list):
        errors.append("edition.signals must be a list")
        signals = []
    elif len(signals) > MAX_SIGNALS:
        errors.append(f"edition.signals must contain at most {MAX_SIGNALS} items")

    seen_ids: set[str] = set()
    ranks: list[int] = []
    for index, signal in enumerate(signals):
        prefix = f"edition.signals[{index}]"
        if not isinstance(signal, dict):
            errors.append(f"{prefix} must be an object")
            continue
        unknown_signal = set(signal) - SIGNAL_REFERENCE_KEYS
        if unknown_signal:
            errors.append(f"{prefix} has unknown keys: {sorted(unknown_signal)}")

        item_id = signal.get("id")
        item = by_id.get(item_id)
        if not isinstance(item_id, str) or item is None:
            errors.append(f"{prefix}.id does not exist in publications ({item_id!r})")
        elif item_id in seen_ids:
            errors.append(f"{prefix}.id is duplicated in the edition ({item_id})")
        else:
            seen_ids.add(item_id)
            if detection_date(item) != edition_date:
                errors.append(f"{prefix}.id does not belong to edition.edition_date")

        rank = signal.get("rank")
        if not isinstance(rank, int) or isinstance(rank, bool):
            errors.append(f"{prefix}.rank must be an integer")
        else:
            ranks.append(rank)

        reason = signal.get("why_it_matters")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{prefix}.why_it_matters must be a non-empty string")
        else:
            count = len(words(reason))
            if not WHY_MIN_WORDS <= count <= WHY_MAX_WORDS:
                errors.append(
                    f"{prefix}.why_it_matters has {count} words "
                    f"(must be {WHY_MIN_WORDS}-{WHY_MAX_WORDS})"
                )
            if contains_act_number(reason):
                errors.append(f"{prefix}.why_it_matters must not contain an act number")

    if ranks and sorted(ranks) != list(range(1, len(signals) + 1)):
        errors.append("edition signal ranks must be contiguous starting at 1")

    lead_id = edition.get("lead_id")
    ranked_lead = next(
        (
            signal.get("id")
            for signal in signals
            if isinstance(signal, dict) and signal.get("rank") == 1
        ),
        None,
    )
    if state == "ready":
        if not today_items:
            errors.append("edition.state ready requires at least one item from the edition date")
        if not signals:
            errors.append("edition.state ready requires at least one signal")
        if lead_id != ranked_lead or not isinstance(lead_id, str):
            errors.append("edition.lead_id must match the rank 1 signal")
    if state == "empty":
        if today_items:
            errors.append("edition.state empty requires zero items from the edition date")
        if signals:
            errors.append("edition.state empty requires an empty signals list")
        if lead_id is not None:
            errors.append("edition.state empty requires a null lead_id")

    if parsed_date is None and isinstance(edition_date, str):
        errors.append("edition.edition_date could not be parsed")
    return errors


def _validate_coverage(coverage: Any, sources: list[Any]) -> list[str]:
    if not isinstance(coverage, dict):
        return ["edition.coverage must be an object"]
    errors: list[str] = []
    unknown = set(coverage) - {"state", "ok", "failed"}
    if unknown:
        errors.append(f"edition.coverage has unknown keys: {sorted(unknown)}")

    source_records = [source for source in sources if isinstance(source, dict)]
    expected_failed = [
        str(source.get("source", "Fuente desconocida"))
        for source in source_records
        if source.get("status") in {"error", "degraded"}
    ]
    expected_ok = sum(source.get("status") == "ok" for source in source_records)
    failed = coverage.get("failed")
    if not isinstance(failed, list) or any(
        not isinstance(source, str) or not source.strip() for source in failed
    ):
        errors.append("edition.coverage.failed must be a list of source names")
    elif failed != expected_failed:
        errors.append("edition.coverage.failed must match sources in error")

    ok_count = coverage.get("ok")
    if not isinstance(ok_count, int) or isinstance(ok_count, bool):
        errors.append("edition.coverage.ok must be an integer")
    elif ok_count != expected_ok:
        errors.append("edition.coverage.ok must match sources in ok state")

    expected_states = {"partial", "degraded"} if expected_failed else {"complete"}
    if coverage.get("state") not in expected_states:
        errors.append(
            "edition.coverage.state must be " + " or ".join(sorted(expected_states))
        )
    return errors


@dataclass(frozen=True)
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_publications_payload(
    payload: dict[str, Any], *, public_only: bool = False, index_only: bool = False
) -> ValidationReport:
    """Valida un corte v8 o una cola privada normalizada.

    Durante la transición, el mismo formato transporta la cola privada y el
    índice público. ``public_only`` es el gate de publicación: prohíbe que un
    registro pendiente o en revisión llegue a un artefacto servido. El valor
    por defecto conserva la lectura de cortes v7/v8 existentes y permite
    validar la cola antes de que se exporte su subconjunto completo.
    """
    errors: list[str] = []
    warnings: list[str] = []
    source_errors: list[str] = []

    schema_version = payload.get("schema_version")
    is_v8 = schema_version == SCHEMA_VERSION
    _validate_top_level(payload, errors)
    items = payload.get("items")
    sources = payload.get("sources")

    if isinstance(items, list):
        expected_total = payload.get("total_items")
        if isinstance(expected_total, int) and expected_total != len(items):
            errors.append(f"total_items is {expected_total}, but items has {len(items)} records")
        seen_item_ids: set[str] = set()
        for index, item in enumerate(items):
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                item_id = item["id"]
                if item_id in seen_item_ids:
                    errors.append(f"items[{index}].id is duplicated ({item_id})")
                seen_item_ids.add(item_id)
            if is_v8:
                if index_only:
                    _validate_public_index_item(index, item, errors)
                else:
                    _validate_item_v8(
                        index, item, errors, warnings, public_only=public_only
                    )
            else:
                _validate_item(index, item, errors, warnings)

    edition = payload.get("edition")
    if edition is not None:
        errors.extend(
            validate_edition(
                edition,
                items if isinstance(items, list) else [],
                sources if isinstance(sources, list) else [],
            )
        )
    elif is_v8:
        errors.append("schema_version 8 requires an edition object")

    if is_v8 and isinstance(edition, dict):
        coverage = edition.get("coverage")
        if isinstance(coverage, dict) and coverage.get("state") == "partial":
            errors.append("schema_version 8 uses edition.coverage.state degraded, not partial")

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
        hard_failures = sum(
            isinstance(source, dict) and source.get("status") == "error" for source in sources
        )
        if sources and hard_failures == len(sources):
            errors.append("all sources are in error state")
        elif source_errors:
            warnings.extend(source_errors)

    return ValidationReport(errors=errors, warnings=warnings, source_errors=source_errors)


def _validate_public_index_item(index: int, item: Any, errors: list[str]) -> None:
    if not isinstance(item, dict):
        errors.append(f"items[{index}] must be an object")
        return
    prefix = f"items[{index}]"
    extra = set(item) - set(PUBLIC_INDEX_FIELDS)
    if extra:
        errors.append(f"{prefix} contains non-index fields: {sorted(extra)}")
    required = {
        "id": str, "source_id": str, "source": str, "url": str,
        "canonical_url": str, "detail_url": str,
        "official_title": str,
        "published_at": str, "official_published_at": str,
        "editorial_status": str, "extraction_status": str,
        "ai_generated": bool,
    }
    for name, expected in required.items():
        if not isinstance(item.get(name), expected):
            errors.append(f"{prefix}.{name} must be {expected.__name__}")
    complete = item.get("editorial_status") == "complete"
    if complete:
        revalidation = item.get("source_revalidation_status")
        if revalidation not in {None, "complete"}:
            errors.append(
                f"{prefix}.source_revalidation_status must be complete when present"
            )
        for name in ("title", "summary_teaser"):
            if not isinstance(item.get(name), str) or not item[name].strip():
                errors.append(f"{prefix}.{name} must be a non-empty string")
        if item.get("extraction_status") != "complete":
            errors.append(f"{prefix}.extraction_status must be complete")
        if item.get("ai_generated") is not True:
            errors.append(f"{prefix}.ai_generated must be true")
        detail_data_url = item.get("detail_data_url")
        if not isinstance(detail_data_url, str):
            errors.append(f"{prefix}.detail_data_url must be a string")
    elif item.get("ai_generated") is True:
        errors.append(f"{prefix}.ai_generated must be false while pending")
    detail_path = item.get("detail_data_url")
    if isinstance(detail_path, str) and detail_path and not _is_safe_detail_data_url(detail_path):
        errors.append(f"{prefix}.detail_data_url must point to a safe detail JSON")
    teaser = item.get("summary_teaser")
    if complete and isinstance(teaser, str) and not 40 <= len(words(teaser)) <= 80:
        errors.append(f"{prefix}.summary_teaser must have 40-80 words")
    title = item.get("title")
    if complete and isinstance(title, str):
        title_words = len(words(title))
        if not TITLE_MIN_WORDS <= title_words <= TITLE_MAX_WORDS:
            errors.append(f"{prefix}.title must have 5-18 words")
        if len(title) > TITLE_MAX_CHARACTERS:
            errors.append(f"{prefix}.title must have at most 120 characters")


def _validate_top_level(payload: dict[str, Any], errors: list[str]) -> None:
    schema_version = payload.get("schema_version")
    if schema_version is not None and schema_version not in {7, SCHEMA_VERSION}:
        errors.append(f"schema_version must be 7 or {SCHEMA_VERSION}")
    if schema_version == SCHEMA_VERSION:
        cut_id = payload.get("cut_id")
        if cut_id is not None and (
            not isinstance(cut_id, str) or not re.fullmatch(r"[0-9a-f]{24}", cut_id)
        ):
            errors.append("cut_id must be a 24-character lowercase hex digest")
        collection_notes = payload.get("collection_notes")
        if not isinstance(collection_notes, dict):
            errors.append("collection_notes must be an object")
        elif not isinstance(collection_notes.get("dof_previous_day_reviewed"), bool):
            errors.append("collection_notes.dof_previous_day_reviewed must be a boolean")
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
        if not LEGACY_SUMMARY_MIN_WORDS <= count <= LEGACY_SUMMARY_MAX_WORDS:
            errors.append(
                f"items[{index}].summary has {count} words "
                f"(must be {LEGACY_SUMMARY_MIN_WORDS}-{LEGACY_SUMMARY_MAX_WORDS})"
            )

    if isinstance(item.get("card_body"), str):
        card_body = item["card_body"]
        for section in CARD_BODY_SECTIONS:
            if section not in card_body:
                errors.append(f"items[{index}].card_body is missing section '{section}'")
        if contains_act_number(card_section_text(card_body, WHAT_PUBLISHED_SECTION)):
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


def _validate_item_v8(
    index: int,
    item: Any,
    errors: list[str],
    warnings: list[str],
    *,
    public_only: bool = False,
) -> None:
    if not isinstance(item, dict):
        errors.append(f"items[{index}] must be an object")
        return
    prefix = f"items[{index}]"
    for field_name, field_type in V8_REQUIRED_ITEM_FIELDS.items():
        value = item.get(field_name)
        if field_type is int and isinstance(value, bool):
            errors.append(f"{prefix}.{field_name} must be int")
        elif not isinstance(value, field_type):
            errors.append(f"{prefix}.{field_name} must be {field_type.__name__}")

    for field_name in (
        "id",
        "source_id",
        "source",
        "official_title",
        "canonical_url",
        "content_hash",
    ):
        if isinstance(item.get(field_name), str) and not item[field_name].strip():
            errors.append(f"{prefix}.{field_name} must be non-empty")

    content_hash = item.get("content_hash")
    if isinstance(content_hash, str) and not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        errors.append(f"{prefix}.content_hash must be a lowercase SHA-256 digest")

    for field_name in ("official_published_at", "published_at"):
        value = item.get(field_name)
        if isinstance(value, str):
            try:
                datetime.fromisoformat(value)
            except ValueError:
                errors.append(f"{prefix}.{field_name} must be an ISO date")
    if item.get("published_at") != item.get("official_published_at"):
        errors.append(f"{prefix}.published_at must mirror official_published_at")

    parsed_times: dict[str, datetime] = {}
    for field_name in ("detected_at", "first_seen_at", "last_seen_at"):
        value = item.get(field_name)
        if not isinstance(value, str):
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"{prefix}.{field_name} must be an ISO datetime")
        else:
            if parsed.tzinfo is None:
                errors.append(f"{prefix}.{field_name} must include a timezone")
            parsed_times[field_name] = parsed
    first_seen = parsed_times.get("first_seen_at")
    last_seen = parsed_times.get("last_seen_at")
    if first_seen is not None and last_seen is not None and first_seen > last_seen:
        errors.append(f"{prefix}.first_seen_at must not be after last_seen_at")

    for url_field in ("url", "canonical_url"):
        value = item.get(url_field)
        if isinstance(value, str) and not value.startswith(("http://", "https://")):
            errors.append(f"{prefix}.{url_field} must be an absolute URL")
    detail_url = item.get("detail_url")
    if isinstance(detail_url, str) and not re.fullmatch(
        r"notas/[0-9a-f]{24}\.html", detail_url
    ):
        errors.append(f"{prefix}.detail_url must point to a stable static note")
    detail_data_url = item.get("detail_data_url")
    if (
        isinstance(detail_data_url, str)
        and detail_data_url
        and not _is_safe_detail_data_url(detail_data_url)
    ):
        errors.append(f"{prefix}.detail_data_url must be a safe relative JSON path")

    identifiers = item.get("official_identifiers")
    if isinstance(identifiers, dict) and (
        not identifiers
        or any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            or not value.strip()
            for key, value in identifiers.items()
        )
    ):
        errors.append(f"{prefix}.official_identifiers must contain non-empty strings")
    evidence = item.get("official_evidence")
    if isinstance(evidence, dict) and (
        not evidence
        or any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            or not value.strip()
            for key, value in evidence.items()
        )
    ):
        errors.append(f"{prefix}.official_evidence must contain non-empty strings")

    extraction_status = item.get("extraction_status")
    editorial_status = item.get("editorial_status")
    if extraction_status not in EXTRACTION_STATUSES:
        errors.append(
            f"{prefix}.extraction_status has an unsupported value"
        )
    if editorial_status not in EDITORIAL_STATUSES:
        errors.append(
            f"{prefix}.editorial_status must be pending, needs_review or complete"
        )
    elif editorial_status != "complete":
        _validate_incomplete_editorial(prefix, item, errors)
    else:
        if extraction_status != "complete":
            errors.append(
                f"{prefix}.extraction_status must be complete when editorial_status is complete"
            )
        _validate_complete_editorial(prefix, item, evidence, errors, warnings)

    ai_generated = item.get("ai_generated")
    if ai_generated is True and (
        extraction_status != "complete" or editorial_status != "complete"
    ):
        errors.append(
            f"{prefix}.ai_generated may be true only when extraction and editorial are complete"
        )
    if public_only and (
        extraction_status != "complete" or editorial_status != "complete"
    ):
        errors.append(f"{prefix} is not eligible for a complete-only public cut")

    if isinstance(item.get("jurisdiction"), str) and item["jurisdiction"] not in {
        "nacional",
        "internacional",
    }:
        errors.append(f"{prefix}.jurisdiction must be nacional or internacional")
    importance = item.get("importance")
    if (
        isinstance(importance, int)
        and not isinstance(importance, bool)
        and not 1 <= importance <= 5
    ):
        errors.append(f"{prefix}.importance must be between 1 and 5")
    for tags_field in ("categories", "topic_tags", "subtopic_tags"):
        tags = item.get(tags_field)
        if isinstance(tags, list):
            for tag_index, tag in enumerate(tags):
                if not isinstance(tag, str) or not tag.strip():
                    errors.append(f"{prefix}.{tags_field}[{tag_index}] must be a string")
    for field_name in (
        "case_number",
        "case_parties",
        "case_status",
        "case_facts",
        "case_treaty",
        "case_claim",
        "case_outcome",
        "case_reasoning",
        "case_amount",
    ):
        if field_name in item and not isinstance(item.get(field_name), str):
            errors.append(f"{prefix}.{field_name} must be str")
    _validate_stateful_case(prefix, item, errors)


def _has_official_evidence_url(evidence: Any) -> bool:
    if not isinstance(evidence, dict):
        return False
    for value in evidence.values():
        if not isinstance(value, str):
            continue
        try:
            parts = urlsplit(value.strip())
        except ValueError:
            continue
        if parts.scheme == "https" and parts.netloc:
            return True
    return False


def _is_safe_detail_data_url(value: str) -> bool:
    return bool(re.fullmatch(r"data/fichas/[0-9a-f]{24}\.json", value))


def _validate_incomplete_editorial(
    prefix: str, item: dict[str, Any], errors: list[str]
) -> None:
    for field_name in (
        "title",
        "summary_teaser",
        "summary",
        "card_body",
        "executive_summary",
        "detailed_summary",
        "impacts",
        "recommended_actions",
        "evidence",
        "coverage",
    ):
        if item.get(field_name) not in (None, "", [], {}):
            errors.append(f"{prefix}.{field_name} must be empty while incomplete")
    reason = item.get("review_reason")
    if not isinstance(reason, str) or not reason.strip():
        errors.append(f"{prefix}.review_reason must explain why review is pending")
    case_facts = item.get("case_facts")
    if isinstance(case_facts, str) and case_facts.strip():
        errors.append(f"{prefix}.case_facts must be empty while needs_review")


def _title_similarity(left: str, right: str) -> float:
    left_tokens = {token.casefold() for token in words(left)}
    right_tokens = {token.casefold() for token in words(right)}
    return len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))


def _validate_complete_editorial(
    prefix: str,
    item: dict[str, Any],
    official_evidence: Any,
    errors: list[str],
    warnings: list[str],
) -> None:
    if item.get("review_reason") is not None:
        errors.append(f"{prefix}.review_reason must be null when complete")
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append(f"{prefix}.title must be non-empty when complete")
    else:
        title_words = len(words(title))
        if not TITLE_MIN_WORDS <= title_words <= TITLE_MAX_WORDS:
            errors.append(
                f"{prefix}.title has {title_words} words "
                f"(must be {TITLE_MIN_WORDS}-{TITLE_MAX_WORDS})"
            )
        if len(title) > TITLE_MAX_CHARACTERS:
            errors.append(
                f"{prefix}.title has {len(title)} characters "
                f"(maximum {TITLE_MAX_CHARACTERS})"
            )
        if re.match(OFFICE_NUMBER_TITLE, title.strip(), flags=re.IGNORECASE):
            errors.append(f"{prefix}.title must lead with substance, not an act number")
        official_title = str(item.get("official_title") or "")
        if _title_similarity(title, official_title) >= 0.8:
            errors.append(f"{prefix}.title must not copy the official title")
        if GENERIC_HEADLINE_RE.search(title) or not (
            SUBSTANTIVE_HEADLINE_RE.search(title) or CONSEQUENCE_NOUN_RE.search(title)
        ):
            errors.append(f"{prefix}.title must state a verifiable change or consequence")
        if str(item.get("source_id") or "") == "5795726":
            normalized = title.casefold()
            if not (
                "cfdi" in normalized
                and "falso" in normalized
                and re.search(r"(?:30|treinta)\s+días|un\s+mes", normalized)
                and re.search(r"revert|dejar\s+sin\s+efecto|efectos?", normalized)
            ):
                errors.append(
                    f"{prefix}.title must state false CFDI, the 30-day term, "
                    "and reversal of effects"
                )

    teaser = item.get("summary_teaser")
    if not isinstance(teaser, str) or not teaser.strip():
        errors.append(f"{prefix}.summary_teaser must be non-empty when complete")
    elif not 40 <= len(words(teaser)) <= 80:
        errors.append(f"{prefix}.summary_teaser must contain 40-80 words")

    summary = item.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append(f"{prefix}.summary must be non-empty when complete")
    else:
        count = len(words(summary))
        if not SUMMARY_MIN_WORDS <= count <= SUMMARY_MAX_WORDS:
            errors.append(
                f"{prefix}.summary has {count} words "
                f"(must be {SUMMARY_MIN_WORDS}-{SUMMARY_MAX_WORDS})"
            )

    card_body = item.get("card_body")
    if not isinstance(card_body, str) or not card_body.strip():
        errors.append(f"{prefix}.card_body must be non-empty when complete")
    else:
        for section in CARD_BODY_SECTIONS:
            if section not in card_body:
                errors.append(f"{prefix}.card_body is missing section '{section}'")
        if contains_act_number(card_section_text(card_body, WHAT_PUBLISHED_SECTION)):
            errors.append(
                f"{prefix}.card_body '{WHAT_PUBLISHED_SECTION}' must not contain an act number"
            )
    if not _has_official_evidence_url(official_evidence):
        errors.append(
            f"{prefix}.official_evidence must contain an official HTTPS URL when complete"
        )

    structured_values = [item.get(detail_field) for detail_field in DETAIL_FIELDS]
    requires_structured = any(
        item.get(marker) not in (None, "")
        for marker in ("source_content_hash", "extracted_text", "extraction_method")
    )
    if requires_structured or any(
        value not in (None, "", [], {}) for value in structured_values
    ):
        for detail_field, value in zip(DETAIL_FIELDS, structured_values, strict=True):
            if value in (None, "", [], {}):
                errors.append(
                    f"{prefix}.{detail_field} is required for a structured complete item"
                )


def validate_structured_public_item(item: dict[str, Any]) -> list[str]:
    """Valida materialidad y trazabilidad de una ficha pública v8."""
    prefix = f"item[{item.get('id', '?')}]"
    errors: list[str] = []

    def paragraphs(value: Any, label: str) -> bool:
        valid = isinstance(value, list) and bool(value) and all(
            isinstance(entry, str) and entry.strip() for entry in value
        )
        if not valid:
            errors.append(f"{prefix}.{label} must be a non-empty paragraph list")
        return valid

    executive = item.get("executive_summary")
    if paragraphs(executive, "executive_summary") and not 1 <= len(executive) <= 4:
        errors.append(f"{prefix}.executive_summary must contain 1-4 paragraphs")
    if isinstance(executive, list) and len(words(" ".join(executive))) > 220:
        errors.append(f"{prefix}.executive_summary must contain at most 220 words")

    detailed = item.get("detailed_summary")
    if not isinstance(detailed, list) or not detailed:
        errors.append(f"{prefix}.detailed_summary must contain sections")
        detailed = []
    impacts = item.get("impacts")
    if not isinstance(impacts, dict) or set(impacts) != {"general", "sectors"}:
        errors.append(f"{prefix}.impacts must be an object")
        impacts = {}
    general = impacts.get("general")
    if not isinstance(general, dict) or set(general) != {"paragraphs", "evidence_refs"}:
        errors.append(f"{prefix}.impacts.general must be an object")
        general = {}
    sectors = impacts.get("sectors")
    if not isinstance(sectors, list) or not sectors:
        errors.append(f"{prefix}.impacts.sectors must name at least one sector")
        sectors = []
    actions = item.get("recommended_actions")
    if not isinstance(actions, list) or not actions:
        errors.append(f"{prefix}.recommended_actions must name affected groups")
        actions = []

    evidence = item.get("evidence")
    evidence_keys = {
        "official_url", "retrieved_at", "content_hash", "extraction_method", "locators"
    }
    if not isinstance(evidence, dict) or set(evidence) != evidence_keys:
        errors.append(f"{prefix}.evidence must be an object")
        evidence = {}
    official_url = evidence.get("official_url")
    if not isinstance(official_url, str) or not official_url.startswith("https://"):
        errors.append(f"{prefix}.evidence.official_url must be HTTPS")
    retrieved_at = evidence.get("retrieved_at")
    try:
        if not isinstance(retrieved_at, str):
            raise ValueError
        datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{prefix}.evidence.retrieved_at must be ISO-8601")
    if not re.fullmatch(r"[a-f0-9]{64}", str(evidence.get("content_hash") or ""), re.I):
        errors.append(f"{prefix}.evidence.content_hash must be SHA-256")
    if (
        not isinstance(evidence.get("extraction_method"), str)
        or not evidence.get("extraction_method")
    ):
        errors.append(f"{prefix}.evidence.extraction_method is required")
    locators = evidence.get("locators")
    if not isinstance(locators, list) or not locators:
        errors.append(f"{prefix}.evidence.locators must be non-empty")
        locators = []
    locator_ids: set[str] = set()
    for locator in locators:
        if not isinstance(locator, dict) or set(locator) != {"id", "label", "location"} or not all(
            isinstance(locator.get(key), str) and locator[key].strip()
            for key in ("id", "label", "location")
        ):
            errors.append(f"{prefix}.evidence contains an invalid locator")
            continue
        if locator["id"] in locator_ids:
            errors.append(f"{prefix}.evidence locator ids must be unique")
        locator_ids.add(locator["id"])

    referenced: set[str] = set()

    def refs(owner: dict[str, Any], label: str) -> None:
        value = owner.get("evidence_refs")
        if not isinstance(value, list) or not value or not all(
            isinstance(ref, str) and ref.strip() for ref in value
        ):
            errors.append(f"{prefix}.{label}.evidence_refs must be non-empty")
            return
        referenced.update(value)

    for index, section in enumerate(detailed):
        if (
            not isinstance(section, dict)
            or set(section) != {"heading", "paragraphs", "evidence_refs"}
            or not isinstance(section.get("heading"), str)
            or not section["heading"].strip()
        ):
            errors.append(f"{prefix}.detailed_summary[{index}] has no heading")
            continue
        paragraphs(section.get("paragraphs"), f"detailed_summary[{index}].paragraphs")
        refs(section, f"detailed_summary[{index}]")
    paragraphs(general.get("paragraphs"), "impacts.general.paragraphs")
    refs(general, "impacts.general")
    for index, sector in enumerate(sectors):
        if (
            not isinstance(sector, dict)
            or set(sector) != {"sector", "paragraphs", "evidence_refs"}
            or not isinstance(sector.get("sector"), str)
            or not sector["sector"].strip()
        ):
            errors.append(f"{prefix}.impacts.sectors[{index}] has no sector")
            continue
        paragraphs(sector.get("paragraphs"), f"impacts.sectors[{index}].paragraphs")
        refs(sector, f"impacts.sectors[{index}]")
    for index, action in enumerate(actions):
        if (
            not isinstance(action, dict)
            or set(action) != {
                "affected_group", "paragraphs", "trigger", "deadline",
                "legal_basis", "evidence_refs"
            }
            or not isinstance(action.get("affected_group"), str)
            or not action["affected_group"].strip()
        ):
            errors.append(f"{prefix}.recommended_actions[{index}] has no affected_group")
            continue
        paragraphs(action.get("paragraphs"), f"recommended_actions[{index}].paragraphs")
        refs(action, f"recommended_actions[{index}]")
    if not referenced.issubset(locator_ids):
        errors.append(f"{prefix}.evidence_refs must resolve to evidence locators")

    coverage = item.get("coverage")
    if not isinstance(coverage, list) or not coverage:
        errors.append(f"{prefix}.coverage must be non-empty")
        coverage = []
    covered: set[str] = set()
    coverage_targets = {
        "executive_summary",
        "detailed_summary",
        "impacts.general",
        "impacts.sectors",
        "recommended_actions",
    }
    for index, row in enumerate(coverage):
        if (
            not isinstance(row, dict)
            or set(row) != {
                "source_ref", "disposition", "target_sections", "reason"
            }
            or row.get("source_ref") not in locator_ids
        ):
            errors.append(f"{prefix}.coverage[{index}] has an invalid source_ref")
            continue
        covered.add(row["source_ref"])
        disposition = row.get("disposition")
        if disposition not in {"summarized", "not_material"}:
            errors.append(f"{prefix}.coverage[{index}] has an invalid disposition")
        targets = row.get("target_sections")
        reason = row.get("reason")
        if disposition == "summarized" and (
            not isinstance(targets, list)
            or not targets
            or any(target not in coverage_targets for target in targets)
            or (reason is not None and not isinstance(reason, str))
        ):
            errors.append(f"{prefix}.coverage[{index}] must name valid target_sections")
        if disposition == "not_material" and (
            targets != [] or not isinstance(reason, str) or not reason.strip()
        ):
            errors.append(
                f"{prefix}.coverage[{index}] must have no targets and justify exclusion"
            )
    if covered != locator_ids:
        errors.append(f"{prefix}.coverage must account for every evidence locator")
    if not isinstance(item.get("detail_data_url"), str) or not item["detail_data_url"]:
        errors.append(f"{prefix}.detail_data_url is required")
    return errors


def _validate_stateful_case(
    prefix: str, item: dict[str, Any], errors: list[str]
) -> None:
    source = item.get("source")
    if source not in {"CIADI", "Secretariado T-MEC"}:
        return
    case_number = item.get("case_number")
    if not isinstance(case_number, str) or not case_number.strip():
        errors.append(f"{prefix}.case_number is required for {source}")
        return
    case_number = case_number.strip()
    source_id = item.get("source_id")
    if source_id != case_number:
        errors.append(f"{prefix}.source_id must equal case_number for {source}")
    identifiers = item.get("official_identifiers")
    if not isinstance(identifiers, dict) or identifiers.get("case_number") != case_number:
        errors.append(
            f"{prefix}.official_identifiers.case_number must equal case_number for {source}"
        )
    canonical_url = item.get("canonical_url")
    evidence = item.get("official_evidence")
    if not isinstance(canonical_url, str) or not isinstance(evidence, dict):
        return

    if source == "Secretariado T-MEC":
        from app.sources.tmec import VALID_STATUSES

        if not re.fullmatch(r"[A-Z]{3}-[A-Z]{3}-\d{4}-[A-Z0-9.-]+", case_number):
            errors.append(f"{prefix}.case_number has an invalid T-MEC format")
        if item.get("case_status") not in VALID_STATUSES:
            errors.append(f"{prefix}.case_status is not a recognized T-MEC status")
        if not _query_identifies(canonical_url, "case", case_number):
            errors.append(f"{prefix}.canonical_url must identify its T-MEC case")
        listing = evidence.get("case_listing")
        if not isinstance(listing, str) or not _query_identifies(
            listing, "case", case_number
        ):
            errors.append(f"{prefix}.official_evidence.case_listing must identify the case")
        status_evidence = evidence.get("status")
        if status_evidence != item.get("case_status"):
            errors.append(f"{prefix}.official_evidence.status must match case_status")
        return

    if not re.fullmatch(
        r"(?:(?:ARB|CONC)(?:\(AF\))?|UNCT)/\d{2,4}/\d+", case_number
    ):
        errors.append(f"{prefix}.case_number has an invalid CIADI format")
    if not _query_identifies(canonical_url, "CaseNo", case_number):
        errors.append(f"{prefix}.canonical_url must identify its CIADI case")
    detail = evidence.get("case_detail")
    if not isinstance(detail, str) or not _query_identifies(detail, "CaseNo", case_number):
        errors.append(f"{prefix}.official_evidence.case_detail must identify the case")
    status_evidence = evidence.get("status")
    if status_evidence and status_evidence != item.get("case_status"):
        errors.append(f"{prefix}.official_evidence.status must match case_status")
    treaty = item.get("case_treaty")
    treaty_evidence = evidence.get("instrument_invoked")
    if (treaty or treaty_evidence) and treaty != treaty_evidence:
        errors.append(
            f"{prefix}.case_treaty must match official_evidence.instrument_invoked"
        )


def _query_identifies(url: str, key: str, expected: str) -> bool:
    try:
        query = parse_qs(urlsplit(url).query)
    except ValueError:
        return False
    return any(
        candidate.casefold() == key.casefold() and expected in values
        for candidate, values in query.items()
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
    if status not in {"ok", "degraded", "error"}:
        errors.append(f"sources[{index}].status must be ok, degraded or error")
    if not isinstance(source.get("items_found"), int):
        errors.append(f"sources[{index}].items_found must be an integer")
    attempts = source.get("attempts")
    if not isinstance(attempts, int) or attempts < 1:
        errors.append(f"sources[{index}].attempts must be a positive integer")
    if status in {"degraded", "error"}:
        message = source.get("error") or "unknown error"
        source_errors.append(f"{name}: {message}")


def validate_freshness(
    payload: dict[str, Any],
    *,
    now: datetime,
    max_age_hours: float,
) -> list[str]:
    """Valida la antigüedad del corte sin imponer un reloj a datos históricos."""
    generated_at = payload.get("generated_at")
    if not isinstance(generated_at, str):
        return ["generated_at must be a string for freshness validation"]
    try:
        generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        return ["generated_at must be an ISO datetime for freshness validation"]
    if generated.tzinfo is None:
        return ["generated_at must include a timezone for freshness validation"]
    if now.tzinfo is None:
        return ["now must include a timezone for freshness validation"]
    age_hours = (now - generated).total_seconds() / 3600
    if age_hours < -0.25:
        return [f"cut generated_at is {-age_hours:.2f} hours in the future"]
    if age_hours > max_age_hours:
        return [
            f"cut is {age_hours:.2f} hours old (maximum {max_age_hours:g} hours)"
        ]
    return []


def validate_site_artifacts(
    manifest_path: Path,
    *,
    root: Path | None = None,
    require_complete_only: bool | None = None,
) -> ValidationReport:
    """Verifica hashes, `cut_id` y referencias cruzadas de un corte v8."""
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = Path(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ValidationReport(errors=[f"manifest cannot be read: {exc}"])
    if not isinstance(manifest, dict):
        return ValidationReport(errors=["manifest must be an object"])
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"manifest.schema_version must be {SCHEMA_VERSION}")
    if require_complete_only is None:
        require_complete_only = manifest.get("publication_policy") == "complete-only"
    if require_complete_only and manifest.get("publication_policy") != "complete-only":
        errors.append("manifest.publication_policy must be complete-only for schema v8")
    cut_id = manifest.get("cut_id")
    if not isinstance(cut_id, str) or not re.fullmatch(r"[0-9a-f]{24}", cut_id):
        errors.append("manifest.cut_id must be a 24-character lowercase hex digest")

    artifact_map = manifest.get("artifacts")
    if not isinstance(artifact_map, dict):
        return ValidationReport(errors=[*errors, "manifest.artifacts must be an object"])
    if manifest.get("artifact_count") != len(artifact_map):
        errors.append("manifest.artifact_count must match manifest.artifacts")

    root = Path(root) if root is not None else manifest_path.parent.parent
    manifest_relative = manifest_path.relative_to(root)
    data_prefix = manifest_relative.parent
    expected_publications = (data_prefix / "publications.json").as_posix()
    expected_edition = (data_prefix / "edition.json").as_posix()
    for required in (expected_publications, expected_edition):
        if required not in artifact_map:
            errors.append(f"manifest.artifacts is missing {required}")

    decoded: dict[str, Any] = {}
    raw_contents: dict[str, bytes] = {}
    for path_text, metadata in artifact_map.items():
        if not isinstance(path_text, str):
            errors.append("manifest artifact paths must be strings")
            continue
        relative = Path(path_text)
        if relative.is_absolute() or ".." in relative.parts:
            errors.append(f"manifest artifact path is unsafe: {path_text!r}")
            continue
        if not isinstance(metadata, dict):
            errors.append(f"manifest.artifacts[{path_text!r}] must be an object")
            continue
        path = root / relative
        try:
            content = path.read_bytes()
        except OSError as exc:
            errors.append(f"artifact {path_text} cannot be read: {exc}")
            continue
        raw_contents[path_text] = content
        actual_hash = hashlib.sha256(content).hexdigest()
        if metadata.get("sha256") != actual_hash:
            errors.append(f"artifact {path_text} hash does not match manifest")
        if metadata.get("bytes") != len(content):
            errors.append(f"artifact {path_text} byte count does not match manifest")
        if path.suffix == ".json":
            try:
                decoded[path_text] = json.loads(content)
            except json.JSONDecodeError as exc:
                errors.append(f"artifact {path_text} is not valid JSON: {exc}")

    publications = decoded.get(expected_publications)
    if isinstance(publications, dict):
        if publications.get("cut_id") != cut_id:
            errors.append("publications.cut_id must match manifest.cut_id")
        report = validate_publications_payload(
            publications, public_only=True, index_only=True
        )
        errors.extend(f"publications: {error}" for error in report.errors)
        warnings.extend(report.warnings)
        if manifest.get("publication_policy") == "complete-only":
            for index, item in enumerate(publications.get("items", [])):
                if not isinstance(item, dict):
                    continue
                detail_path = item.get("detail_data_url")
                expected_detail = (
                    f"data/fichas/{item_key(str(item.get('id') or ''))}.json"
                )
                if detail_path != expected_detail:
                    errors.append(
                        f"publications: items[{index}].detail_data_url must be "
                        f"{expected_detail}"
                    )
    edition = decoded.get(expected_edition)
    if isinstance(edition, dict):
        if edition.get("cut_id") != cut_id:
            errors.append("edition.cut_id must match manifest.cut_id")
        if edition.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"edition.schema_version must be {SCHEMA_VERSION}")
        if edition.get("generated_at") != manifest.get("generated_at"):
            errors.append("edition.generated_at must match manifest.generated_at")
        if edition.get("edition_date") != manifest.get("edition_date"):
            errors.append("edition.edition_date must match manifest.edition_date")
        if isinstance(publications, dict) and edition != build_edition_artifact(publications):
            errors.append("edition must match the referenced publication signals exactly")
    if isinstance(publications, dict):
        if publications.get("generated_at") != manifest.get("generated_at"):
            errors.append("publications.generated_at must match manifest.generated_at")
        publication_edition = publications.get("edition")
        if isinstance(publication_edition, dict) and publication_edition.get(
            "edition_date"
        ) != manifest.get("edition_date"):
            errors.append("publications.edition_date must match manifest.edition_date")

    item_records: dict[str, dict[str, Any]] = {}
    for path_text, value in decoded.items():
        is_item = "/items/" in f"/{path_text}" and path_text.endswith(".json")
        if not is_item:
            continue
        if not isinstance(value, dict):
            errors.append(f"{path_text} must be an item envelope")
            continue
        if value.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"{path_text}.schema_version must be {SCHEMA_VERSION}")
        if value.get("cut_id") != cut_id:
            errors.append(f"{path_text}.cut_id must match manifest.cut_id")
        item = value.get("item")
        if not isinstance(item, dict):
            errors.append(f"{path_text}.item must be an object")
            continue
        allowed_permanent = set(PUBLIC_PERMANENT_FIELDS)
        if not require_complete_only:
            allowed_permanent.add("detail_markdown")
        permanent_extra = set(item) - allowed_permanent
        if permanent_extra:
            errors.append(
                f"{path_text}.item contains non-public fields: {sorted(permanent_extra)}"
            )
        item_errors: list[str] = []
        item_warnings: list[str] = []
        complete_only_policy = manifest.get("publication_policy") == "complete-only"
        _validate_item_v8(
            0,
            item,
            item_errors,
            item_warnings,
            public_only=complete_only_policy,
        )
        if complete_only_policy:
            item_errors.extend(validate_structured_public_item(item))
        for private_field in (
            "extracted_text",
            "extraction_diagnostics",
            "source_content_hash",
        ):
            if private_field in item:
                item_errors.append(f"private field {private_field} must not be public")
        errors.extend(f"{path_text}: {error}" for error in item_errors)
        warnings.extend(f"{path_text}: {warning}" for warning in item_warnings)
        item_id = item.get("id")
        if isinstance(item_id, str):
            if item_id in item_records:
                errors.append(f"duplicate permanent item id: {item_id}")
            item_records[item_id] = item
        expected_name = hashlib.sha256(str(item_id or "").encode()).hexdigest()[:24]
        if Path(path_text).stem != expected_name:
            errors.append(f"{path_text} filename must match the stable item identity")
        detail_url = item.get("detail_url")
        if isinstance(detail_url, str) and detail_url not in artifact_map:
            errors.append(f"{path_text} references missing detail artifact {detail_url}")
        elif isinstance(detail_url, str):
            note = raw_contents.get(detail_url, b"").decode("utf-8", errors="replace")
            expected_title = html.escape(
                str(item.get("title") or item.get("official_title") or "")
            )
            expected_canonical = (
                "https://bpop06.github.io/radar-regulatorio-mx/" + detail_url
            )
            if expected_title and expected_title not in note:
                errors.append(f"{detail_url} does not render its permanent item title")
            if expected_canonical not in note:
                errors.append(f"{detail_url} does not declare its stable canonical URL")
            expected_stylesheet = f'../styles.css?v={STATIC_ASSET_VERSION}'
            if f'<link rel="stylesheet" href="{expected_stylesheet}">' not in note:
                errors.append(
                    f"{detail_url} must use the versioned site stylesheet "
                    f"{expected_stylesheet}"
                )
            json_ld_match = re.search(
                r'<script type="application/ld\+json">(.*?)</script>',
                note,
                flags=re.DOTALL,
            )
            if json_ld_match is None:
                errors.append(f"{detail_url} must declare Article JSON-LD")
            else:
                try:
                    article = json.loads(json_ld_match.group(1))
                except json.JSONDecodeError as exc:
                    errors.append(f"{detail_url} has invalid JSON-LD: {exc}")
                else:
                    main_entity = (
                        article.get("mainEntityOfPage") if isinstance(article, dict) else None
                    )
                    if not (
                        isinstance(article, dict)
                        and article.get("@context") == "https://schema.org"
                        and article.get("@type") == "Article"
                        and article.get("headline")
                        == str(item.get("title") or item.get("official_title") or "")
                        and article.get("url") == expected_canonical
                        and isinstance(main_entity, dict)
                        and main_entity.get("@id") == expected_canonical
                    ):
                        errors.append(
                            f"{detail_url} JSON-LD must describe its canonical Article"
                        )
        detail_data_url = item.get("detail_data_url")
        if (
            manifest.get("publication_policy") == "complete-only"
            and
            item.get("extraction_status") == "complete"
            and item.get("editorial_status") == "complete"
            and (not isinstance(detail_data_url, str) or detail_data_url not in artifact_map)
        ):
            errors.append(
                f"{path_text} references missing structured detail {detail_data_url}"
            )

    for path_text, value in decoded.items():
        is_detail = "/fichas/" in f"/{path_text}" and path_text.endswith(".json")
        if not is_detail:
            continue
        if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"{path_text} must be a schema v8 detail envelope")
            continue
        if value.get("cut_id") != cut_id:
            errors.append(f"{path_text}.cut_id must match manifest.cut_id")
        item = value.get("item")
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            errors.append(f"{path_text}.item must identify its publication")
            continue
        detail_item_extra = set(item) - set(PUBLIC_INDEX_FIELDS)
        if detail_item_extra:
            errors.append(
                f"{path_text}.item contains non-index fields: {sorted(detail_item_extra)}"
            )
        allowed_envelope = {"schema_version", "cut_id", "item", *DETAIL_FIELDS}
        detail_extra = set(value) - allowed_envelope
        if detail_extra:
            errors.append(f"{path_text} contains unknown fields: {sorted(detail_extra)}")
        permanent = item_records.get(item["id"])
        if permanent is None:
            errors.append(f"{path_text} has no permanent item")
            continue
        expected_path = permanent.get("detail_data_url")
        if expected_path != path_text:
            errors.append(f"{path_text} does not match its permanent detail_data_url")
        expected_detail_item = {
            field: permanent.get(field)
            for field in PUBLIC_INDEX_FIELDS
            if field in permanent
        }
        if item != expected_detail_item:
            errors.append(f"{path_text}.item differs from permanent public projection")
        for detail_field in DETAIL_FIELDS:
            if value.get(detail_field) != permanent.get(detail_field):
                errors.append(f"{path_text}.{detail_field} differs from permanent item")

    if isinstance(publications, dict):
        for current in publications.get("items", []):
            if not isinstance(current, dict) or not isinstance(current.get("id"), str):
                continue
            permanent = item_records.get(current["id"])
            if permanent is None or any(
                permanent.get(field) != value
                for field, value in current.items()
            ):
                errors.append(
                    f"publications item {current['id']} must match its permanent item envelope"
                )

    archive_months = manifest.get("archive_months")
    if not isinstance(archive_months, list) or any(
        not isinstance(month, str) or not re.fullmatch(r"\d{4}-\d{2}", month)
        for month in (archive_months if isinstance(archive_months, list) else [])
    ):
        errors.append("manifest.archive_months must contain YYYY-MM strings")
        archive_months = []
    archived_ids: set[str] = set()
    for month in archive_months:
        archive_path = (data_prefix / "archive" / f"{month}.json").as_posix()
        archive = decoded.get(archive_path)
        if not isinstance(archive, dict):
            errors.append(f"archive index missing or invalid: {archive_path}")
            continue
        if archive.get("cut_id") != cut_id:
            errors.append(f"{archive_path}.cut_id must match manifest.cut_id")
        if archive.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"{archive_path}.schema_version must be {SCHEMA_VERSION}")
        if archive.get("month") != month:
            errors.append(f"{archive_path}.month must be {month}")
        items = archive.get("items")
        if not isinstance(items, list) or archive.get("total_items") != len(items):
            errors.append(f"{archive_path}.total_items must match items")
        if isinstance(items, list):
            for index, summary in enumerate(items):
                if not isinstance(summary, dict) or not isinstance(summary.get("id"), str):
                    errors.append(f"{archive_path}.items[{index}] must have a string id")
                    continue
                item_id = summary["id"]
                if item_id in archived_ids:
                    errors.append(f"archive item id is duplicated: {item_id}")
                archived_ids.add(item_id)
                permanent = item_records.get(item_id)
                if permanent is None:
                    errors.append(f"{archive_path}.items[{index}] has no permanent item")
                    continue
                expected_month = str(
                    permanent.get("official_published_at")
                    or permanent.get("published_at")
                    or ""
                )[:7]
                if expected_month != month:
                    errors.append(f"{archive_path}.items[{index}] belongs to {expected_month}")
                if any(permanent.get(key) != value for key, value in summary.items()):
                    errors.append(
                        f"{archive_path}.items[{index}] does not match its permanent item"
                    )
    missing_archive = set(item_records) - archived_ids
    if missing_archive:
        errors.append(
            "permanent items missing from monthly archive: "
            + ", ".join(sorted(missing_archive)[:5])
        )
    expected_notes = {
        str(item.get("detail_url"))
        for item in item_records.values()
        if isinstance(item.get("detail_url"), str)
    }
    actual_notes = {
        path_text for path_text in artifact_map if path_text.startswith("notas/")
    }
    if actual_notes != expected_notes:
        errors.append("manifest note artifacts must match permanent item detail URLs")

    state_prefix = (data_prefix / "state").as_posix().rstrip("/") + "/"
    state_names: set[str] = set()
    raw_states: dict[str, dict[str, Any]] = {}
    for path_text, value in decoded.items():
        if not path_text.startswith(state_prefix) or not path_text.endswith(".json"):
            continue
        state_names.add(Path(path_text).stem)
        if not isinstance(value, dict):
            errors.append(f"{path_text} must be a state envelope")
            continue
        if value.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"{path_text}.schema_version must be {SCHEMA_VERSION}")
        if value.get("cut_id") != cut_id:
            errors.append(f"{path_text}.cut_id must match manifest.cut_id")
        if value.get("source") != Path(path_text).stem:
            errors.append(f"{path_text}.source must match its filename")
        if not isinstance(value.get("state"), dict):
            errors.append(f"{path_text}.state must be an object")
        else:
            raw_states[Path(path_text).stem] = value["state"]
    if isinstance(publications, dict):
        source_names = {
            source.get("source")
            for source in publications.get("sources", [])
            if isinstance(source, dict)
        }
        required_states = {
            state
            for source, state in (("CIADI", "icsid"), ("Secretariado T-MEC", "tmec"))
            if source in source_names
        }
        if missing_states := required_states - state_names:
            errors.append(
                "manifest is missing transactional source state: "
                + ", ".join(sorted(missing_states))
            )
        expected_cut_id = compute_cut_id(publications, item_records.values(), raw_states)
        if cut_id != expected_cut_id:
            errors.append("manifest.cut_id does not match transactional artifact content")
    return ValidationReport(errors=errors, warnings=warnings)
