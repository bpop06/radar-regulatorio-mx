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

# Un título editorial se rige por la temática, no por el número de acto.
OFFICE_NUMBER_TITLE = r"^(oficio|acuerdo|circular|resoluci[oó]n|expediente)\s+[A-Z0-9/.-]*\d"


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

    if isinstance(item.get("summary"), str):
        count = len(words(item["summary"]))
        if count != 30:
            errors.append(f"items[{index}].summary has {count} words")

    if isinstance(item.get("card_body"), str):
        for section in CARD_BODY_SECTIONS:
            if section not in item["card_body"]:
                errors.append(f"items[{index}].card_body is missing section '{section}'")

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
        if "\n\n## " not in markdown:
            errors.append(f"items[{index}].detail_markdown must include section headings")


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
