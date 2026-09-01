from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.edition import (
    load_manifested_items,
    write_site_artifacts,
    write_site_artifacts_legacy,
)
from app.validation import validate_publications_payload, validate_site_artifacts


class RetractionError(RuntimeError):
    """El retiro solicitado no puede aplicarse al corte confirmado."""


PRIVATE_LEDGER_VERSION = 1
PUBLIC_TOMBSTONE_VERSION = 2


def retract_items(
    publications_path: Path,
    item_ids: list[str],
    *,
    reason: str,
    edition_path: Path | None = None,
    ledger_path: Path | None = None,
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
    payload_report = validate_publications_payload(payload, index_only=True)
    if not payload_report.ok:
        raise RetractionError(
            "el payload base no es válido: " + "; ".join(payload_report.errors[:5])
        )
    private_ledger_path = _private_ledger_path(ledger_path)
    existing = _existing_ledger(private_ledger_path)
    now = datetime.now(UTC).isoformat()
    entries = deepcopy(existing.get("items", {}))
    for item_id in requested:
        entries[item_id] = {
            "reason": reason,
            "retracted_at": now,
            "record": permanent[item_id],
        }
    payload["items"] = [
        deepcopy(permanent[item["id"]])
        for item in payload.get("items", [])
        if isinstance(item, dict) and item.get("id") in permanent
    ]
    payload["_retired_item_ids"] = list(requested)
    payload["_pending_state"] = {
        "retractions": _public_tombstones(entries)
    }
    payload["_preserve_edition"] = False
    _write_retraction_artifacts(
        payload,
        publications_path,
        edition_path,
        ledger_path=private_ledger_path,
        entries=entries,
        previous=existing,
    )
    return len(requested)


def restore_items(
    publications_path: Path,
    item_ids: list[str],
    *,
    edition_path: Path | None = None,
    ledger_path: Path | None = None,
) -> int:
    publications_path = Path(publications_path)
    requested = tuple(dict.fromkeys(item_id.strip() for item_id in item_ids if item_id.strip()))
    if not requested:
        raise RetractionError("se requiere al menos un id")
    manifest_path = publications_path.with_name("manifest.json")
    report = validate_site_artifacts(manifest_path)
    legacy_collection_notes = report.errors == [
        "publications: collection_notes must be an object"
    ]
    if not report.ok and not legacy_collection_notes:
        raise RetractionError("el corte base no es válido: " + "; ".join(report.errors[:5]))
    private_ledger_path = _private_ledger_path(ledger_path)
    existing = _existing_ledger(private_ledger_path)
    entries = deepcopy(existing.get("items", {}))
    missing = [item_id for item_id in requested if item_id not in entries]
    if missing:
        raise RetractionError("ids sin retiro registrado: " + ", ".join(missing))

    restored: list[dict[str, Any]] = []
    for item_id in requested:
        record = entries.get(item_id, {}).get("record")
        if not isinstance(record, dict):
            raise RetractionError("el registro privado de retiro no permite restauración")
        restored.append(deepcopy(record))
        entries.pop(item_id)
    payload = json.loads(publications_path.read_text(encoding="utf-8"))
    permanent = load_manifested_items(publications_path)
    current_ids = {
        item.get("id")
        for item in payload.get("items", [])
        if isinstance(item, dict)
    }
    payload["items"] = [
        deepcopy(permanent[item["id"]])
        for item in payload.get("items", [])
        if isinstance(item, dict) and item.get("id") in permanent
    ]
    payload["_historical_items"] = [
        item for item in restored if item.get("id") not in current_ids
    ]
    payload["_pending_state"] = {
        "retractions": _public_tombstones(entries)
    }
    payload["_preserve_edition"] = False
    _write_retraction_artifacts(
        payload,
        publications_path,
        edition_path,
        ledger_path=private_ledger_path,
        entries=entries,
        previous=existing,
    )
    return len(requested)


def _private_ledger_path(value: Path | None) -> Path:
    if value is not None:
        return Path(value).expanduser()
    state_dir = os.getenv("RADAR_STATE_DIR")
    if not state_dir:
        raise RetractionError("se requiere una bitácora privada de retiros fuera del sitio público")
    return Path(state_dir).expanduser() / "retractions.json"


def _existing_ledger(path: Path) -> dict[str, Any]:
    """Read rollback material only from the configured private state directory."""

    path = Path(path)
    if not path.exists():
        return {"version": PRIVATE_LEDGER_VERSION, "items": {}}
    if path.is_symlink():
        raise RetractionError("la bitácora privada de retiros no puede ser un enlace simbólico")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RetractionError("la bitácora privada de retiros es inválida") from exc
    if (
        not isinstance(state, dict)
        or state.get("version") != PRIVATE_LEDGER_VERSION
        or not isinstance(state.get("items"), dict)
    ):
        raise RetractionError("la bitácora privada de retiros es inválida")
    return state


def _public_tombstones(entries: dict[str, Any]) -> dict[str, Any]:
    """Project only opaque retirement metadata to a public v8 artifact."""

    items: dict[str, dict[str, str]] = {}
    for item_id, entry in entries.items():
        if not isinstance(item_id, str) or not isinstance(entry, dict):
            continue
        retracted_at = entry.get("retracted_at")
        if isinstance(retracted_at, str) and retracted_at:
            items[item_id] = {"retracted_at": retracted_at}
    return {"version": PUBLIC_TOMBSTONE_VERSION, "items": items}


def _write_private_ledger(path: Path, entries: dict[str, Any]) -> None:
    path = Path(path)
    parent = path.parent
    created_parent = not parent.exists()
    parent.mkdir(parents=True, exist_ok=True)
    if created_parent:
        parent.chmod(0o700)
    if path.exists() and path.is_symlink():
        raise RetractionError("la bitácora privada de retiros no puede ser un enlace simbólico")
    encoded = (
        json.dumps(
            {"version": PRIVATE_LEDGER_VERSION, "items": entries},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".retractions-", dir=parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except OSError as exc:
        raise RetractionError("no se pudo actualizar la bitácora privada de retiros") from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_retraction_artifacts(
    payload: dict[str, Any],
    publications_path: Path,
    edition_path: Path | None,
    *,
    ledger_path: Path,
    entries: dict[str, Any],
    previous: dict[str, Any],
) -> None:
    """Conserva compatibilidad sólo al operar un corte explícitamente legacy."""
    try:
        manifest = json.loads(
            publications_path.with_name("manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        manifest = {}
    _write_private_ledger(ledger_path, entries)
    try:
        if manifest.get("publication_policy") == "legacy-compatible":
            write_site_artifacts_legacy(payload, publications_path, edition_path)
        else:
            write_site_artifacts(payload, publications_path, edition_path)
    except Exception:
        # La publicación fallida no debe dejar rollback material que afirme un
        # retiro inexistente. La restauración es best-effort pero fail-closed:
        # si también falla, se propaga el problema antes de declarar éxito.
        _write_private_ledger(ledger_path, dict(previous.get("items", {})))
        raise
