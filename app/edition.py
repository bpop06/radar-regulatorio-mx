from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.text import words

SCHEMA_VERSION = 7
MAX_SIGNALS = 7
WHY_MIN_WORDS = 12
WHY_MAX_WORDS = 32
DEFAULT_TIMEZONE = "America/Mexico_City"

EDITION_KEYS = {
    "edition_date",
    "state",
    "coverage",
    "total_today",
    "last_available_date",
    "lead_id",
    "signals",
}
SIGNAL_REFERENCE_KEYS = {"id", "rank", "why_it_matters"}

PUBLIC_SIGNAL_FIELDS = (
    "id",
    "title",
    "summary",
    "source",
    "url",
    "detail_url",
    "published_at",
    "issuing_body",
    "categories",
    "importance",
    "relevance_score",
    "jurisdiction",
    "country_or_org",
)


def edition_date_from_generated_at(
    generated_at: Any,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> date:
    """Obtiene la fecha editorial del corte en la zona horaria del producto."""
    try:
        local_tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        # Windows no distribuye siempre la base IANA. Ciudad de México usa
        # UTC-6 permanente desde octubre de 2022; el fallback mantiene el
        # contrato diario correcto sin exigir tzdata en producción.
        local_tz = (
            timezone(timedelta(hours=-6), name=DEFAULT_TIMEZONE)
            if timezone_name == DEFAULT_TIMEZONE
            else None
        )

    if isinstance(generated_at, str):
        try:
            instant = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            instant = None
        if instant is not None:
            if instant.tzinfo is None:
                return instant.date()
            if local_tz is not None:
                return instant.astimezone(local_tz).date()
            return instant.date()

    if local_tz is not None:
        return datetime.now(local_tz).date()
    return date.today()


def prepare_payload(
    payload: dict[str, Any],
    timezone_name: str = DEFAULT_TIMEZONE,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Agrega o regenera el contrato v7 de edición sin eliminar campos legados."""
    result = deepcopy(payload)
    result["schema_version"] = SCHEMA_VERSION
    if force or not isinstance(result.get("edition"), dict):
        edition_date = edition_date_from_generated_at(result.get("generated_at"), timezone_name)
        result["edition"] = build_default_edition(result, edition_date)
    return result


def build_default_edition(payload: dict[str, Any], edition_date: date) -> dict[str, Any]:
    items = [item for item in payload.get("items", []) if isinstance(item, dict)]
    edition_iso = edition_date.isoformat()
    today_items = [item for item in items if item.get("published_at") == edition_iso]
    sources = [source for source in payload.get("sources", []) if isinstance(source, dict)]
    failed = [
        str(source.get("source", "Fuente desconocida"))
        for source in sources
        if source.get("status") == "error"
    ]
    ok_count = sum(source.get("status") == "ok" for source in sources)

    selected = _select_diverse_signals(today_items)
    digest_reasons = _digest_reason_map(payload.get("digest"))
    signal_refs = [
        {
            "id": item["id"],
            "rank": rank,
            "why_it_matters": _bounded_reason(
                digest_reasons.get(item["id"]) or item.get("summary", "")
            ),
        }
        for rank, item in enumerate(selected, start=1)
    ]

    dates = sorted(
        {
            item.get("published_at")
            for item in items
            if isinstance(item.get("published_at"), str)
        },
        reverse=True,
    )
    return {
        "edition_date": edition_iso,
        "state": "ready" if today_items else "empty",
        "coverage": {
            "state": "partial" if failed else "complete",
            "ok": ok_count,
            "failed": failed,
        },
        "total_today": len(today_items),
        "last_available_date": dates[0] if dates else None,
        "lead_id": signal_refs[0]["id"] if signal_refs else None,
        "signals": signal_refs,
    }


def build_edition_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    """Desnormaliza sólo las señales visibles para la carga inicial de Hoy."""
    edition = payload.get("edition") if isinstance(payload.get("edition"), dict) else {}
    by_id = {
        item.get("id"): item
        for item in payload.get("items", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    signals: list[dict[str, Any]] = []
    for reference in edition.get("signals", []):
        if not isinstance(reference, dict):
            continue
        item = by_id.get(reference.get("id"))
        if item is None:
            continue
        signal = {
            field: deepcopy(item.get(field))
            for field in PUBLIC_SIGNAL_FIELDS
            if field in item
        }
        signal["rank"] = reference.get("rank")
        signal["why_it_matters"] = reference.get("why_it_matters", "")
        signals.append(signal)

    coverage = edition.get("coverage") if isinstance(edition.get("coverage"), dict) else {}
    return {
        "schema_version": payload.get("schema_version", SCHEMA_VERSION),
        "edition_date": edition.get("edition_date"),
        "generated_at": payload.get("generated_at"),
        "state": edition.get("state", "empty"),
        "coverage": deepcopy(coverage),
        "total_today": edition.get("total_today", 0),
        "last_available_date": edition.get("last_available_date"),
        "lead_id": edition.get("lead_id"),
        "signals": signals,
    }


def write_site_artifacts(
    payload: dict[str, Any],
    publications_path: Path,
    edition_path: Path | None = None,
) -> None:
    """Escribe el contrato completo y el corte ligero usando reemplazo atómico."""
    edition_path = edition_path or publications_path.with_name("edition.json")
    publications_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    edition_text = json.dumps(build_edition_artifact(payload), ensure_ascii=False, indent=2) + "\n"

    publications_path.parent.mkdir(parents=True, exist_ok=True)
    edition_path.parent.mkdir(parents=True, exist_ok=True)
    publications_temp = publications_path.with_suffix(publications_path.suffix + ".tmp")
    edition_temp = edition_path.with_suffix(edition_path.suffix + ".tmp")
    publications_temp.write_text(publications_text, encoding="utf-8")
    edition_temp.write_text(edition_text, encoding="utf-8")
    edition_temp.replace(edition_path)
    publications_temp.replace(publications_path)


def _select_diverse_signals(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        items,
        key=lambda item: (
            -_plain_int(item.get("importance")),
            -_plain_int(item.get("relevance_score")),
            str(item.get("id", "")),
        ),
    )
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    bodies: Counter[str] = Counter()
    categories: Counter[str] = Counter()

    for item in ordered:
        body = str(item.get("issuing_body") or item.get("source") or "Sin órgano")
        category = _primary_category(item)
        if bodies[body] >= 2 or categories[category] >= 2:
            continue
        selected.append(item)
        selected_ids.add(str(item.get("id", "")))
        bodies[body] += 1
        categories[category] += 1
        if len(selected) == MAX_SIGNALS:
            return selected

    for item in ordered:
        item_id = str(item.get("id", ""))
        if item_id in selected_ids:
            continue
        selected.append(item)
        if len(selected) == MAX_SIGNALS:
            break
    return selected


def _primary_category(item: dict[str, Any]) -> str:
    categories = item.get("categories")
    if isinstance(categories, list) and categories and isinstance(categories[0], str):
        return categories[0]
    return "Sin materia"


def _digest_reason_map(digest: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(digest, dict):
        return result
    for group in digest.get("groups", []):
        if not isinstance(group, dict):
            continue
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            theme = item.get("theme")
            if isinstance(item_id, str) and isinstance(theme, str):
                result[item_id] = theme
    return result


def _bounded_reason(value: Any) -> str:
    selected = words(value if isinstance(value, str) else "")
    if not selected:
        selected = words(
            "Actualización oficial seleccionada por su importancia jurídica y relevancia "
            "para el seguimiento regulatorio del día"
        )
    if len(selected) < WHY_MIN_WORDS:
        selected.extend(
            words("Revisa la fuente primaria para confirmar su alcance jurídico aplicable")
        )
    return " ".join(selected[:WHY_MAX_WORDS]).rstrip(" .,;:") + "."


def _plain_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
