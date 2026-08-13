from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.edition import load_manifested_items, write_site_artifacts
from app.state import load_committed_state
from app.validation import validate_publications_payload, validate_site_artifacts


class RetractionError(RuntimeError):
    """El retiro solicitado no puede aplicarse al corte confirmado."""


def retract_items(
    publications_path: Path,
    item_ids: list[str],
    *,
    reason: str,
    edition_path: Path | None = None,
) -> int:
    publications_path = Path(publications_path)
    reason = reason.strip()
    requested = tuple(dict.fromkeys(item_id.strip() for item_id in item_ids if item_id.strip()))
    if not requested:
        raise RetractionError("se requiere al menos un id")
    if not reason:
        raise RetractionError("se requiere un motivo de retiro")

    manifest_path = publications_path.with_name("manifest.json")
    report = validate_site_artifacts(manifest_path)
    legacy_collection_notes = report.errors == [
        "publications: collection_notes must be an object"
    ]
    if not report.ok and not legacy_collection_notes:
        raise RetractionError("el corte base no es válido: " + "; ".join(report.errors[:5]))
    permanent = load_manifested_items(publications_path)
    missing = [item_id for item_id in requested if item_id not in permanent]
    if missing:
        raise RetractionError("ids no presentes en el corte: " + ", ".join(missing))

    payload = json.loads(publications_path.read_text(encoding="utf-8"))
    payload.setdefault("collection_notes", {"dof_previous_day_reviewed": False})
    payload_report = validate_publications_payload(payload)
    if not payload_report.ok:
        raise RetractionError(
            "el payload base no es válido: " + "; ".join(payload_report.errors[:5])
        )
    existing = _existing_ledger(publications_path)
    now = datetime.now(UTC).isoformat()
    entries = deepcopy(existing.get("items", {}))
    for item_id in requested:
        entries[item_id] = {
            "reason": reason,
            "retracted_at": now,
            "record": permanent[item_id],
        }
    payload["_retired_item_ids"] = list(requested)
    payload["_pending_state"] = {
        "retractions": {"version": 1, "items": entries}
    }
    payload["_preserve_edition"] = False
    write_site_artifacts(payload, publications_path, edition_path)
    return len(requested)


def restore_items(
    publications_path: Path,
    item_ids: list[str],
    *,
    edition_path: Path | None = None,
) -> int:
    publications_path = Path(publications_path)
    requested = tuple(dict.fromkeys(item_id.strip() for item_id in item_ids if item_id.strip()))
    if not requested:
        raise RetractionError("se requiere al menos un id")
    existing = _existing_ledger(publications_path)
    entries = deepcopy(existing.get("items", {}))
    missing = [item_id for item_id in requested if item_id not in entries]
    if missing:
        raise RetractionError("ids sin retiro registrado: " + ", ".join(missing))

    restored = [deepcopy(entries.pop(item_id)["record"]) for item_id in requested]
    payload = json.loads(publications_path.read_text(encoding="utf-8"))
    current_ids = {
        item.get("id")
        for item in payload.get("items", [])
        if isinstance(item, dict)
    }
    payload["_historical_items"] = [
        item for item in restored if item.get("id") not in current_ids
    ]
    payload["_pending_state"] = {
        "retractions": {"version": 1, "items": entries}
    }
    payload["_preserve_edition"] = False
    write_site_artifacts(payload, publications_path, edition_path)
    return len(requested)


def _existing_ledger(publications_path: Path) -> dict[str, Any]:
    state_path = publications_path.parent / "state/retractions.json"
    if not state_path.exists():
        return {"version": 1, "items": {}}
    state = load_committed_state(state_path, source="retractions")
    if not isinstance(state, dict) or not isinstance(state.get("items"), dict):
        raise RetractionError(
            "el registro de retiros no pertenece al corte confirmado o es inválido"
        )
    return state
