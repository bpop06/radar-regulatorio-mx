from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class ManifestContext:
    status: Literal["absent", "legacy", "v8", "invalid"]
    path: Path
    site_root: Path
    payload: dict[str, Any] | None = None


def load_committed_state(path: Path, *, source: str) -> dict[str, Any] | None:
    """Load state only when it belongs to the manifest's committed cut.

    A v8 state envelope is never authoritative by itself. The manifest is the
    transaction commit marker, so the envelope must be listed there with the
    exact bytes and digest that the manifest committed. Raw v7 snapshots are
    accepted only while no v8 manifest exists, for one-time migration.
    """

    path = Path(path)
    try:
        content = path.read_bytes()
        payload = json.loads(content)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    context = manifest_context(path)
    if _is_state_envelope(payload):
        if context.status != "v8" or context.payload is None:
            return None
        if not _committed_envelope(
            path,
            content,
            payload,
            source=source,
            context=context,
        ):
            return None
        state = payload.get("state")
        return state if isinstance(state, dict) else None

    # Once v8 exists (or its commit marker is corrupt), an unversioned file
    # cannot replace the state committed by that manifest.
    if context.status in {"v8", "invalid"}:
        return None
    return payload


def manifest_context(state_path: Path) -> ManifestContext:
    """Locate and classify the manifest governing a state/snapshot path."""

    state_path = Path(state_path)
    data_dir = (
        state_path.parent.parent
        if state_path.parent.name == "state"
        else state_path.parent
    )
    site_root = data_dir.parent if data_dir.name == "data" else data_dir
    manifest_path = data_dir / "manifest.json"
    if not manifest_path.exists():
        return ManifestContext("absent", manifest_path, site_root)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ManifestContext("invalid", manifest_path, site_root)
    if not isinstance(payload, dict):
        return ManifestContext("invalid", manifest_path, site_root)
    if payload.get("schema_version") == 8:
        return ManifestContext("v8", manifest_path, site_root, payload)
    return ManifestContext("legacy", manifest_path, site_root, payload)


def _is_state_envelope(payload: dict[str, Any]) -> bool:
    return any(
        key in payload for key in ("schema_version", "cut_id", "source", "state")
    )


def _committed_envelope(
    path: Path,
    content: bytes,
    envelope: dict[str, Any],
    *,
    source: str,
    context: ManifestContext,
) -> bool:
    manifest = context.payload or {}
    manifest_cut = manifest.get("cut_id")
    if not isinstance(manifest_cut, str) or not manifest_cut:
        return False
    if envelope.get("schema_version") != 8:
        return False
    if envelope.get("cut_id") != manifest_cut or envelope.get("source") != source:
        return False
    if not isinstance(envelope.get("state"), dict):
        return False

    try:
        relative = path.resolve().relative_to(context.site_root.resolve()).as_posix()
    except (OSError, ValueError):
        return False
    artifacts = manifest.get("artifacts")
    metadata = artifacts.get(relative) if isinstance(artifacts, dict) else None
    if not isinstance(metadata, dict):
        return False
    expected_hash = metadata.get("sha256")
    expected_bytes = metadata.get("bytes")
    return (
        isinstance(expected_hash, str)
        and expected_hash == hashlib.sha256(content).hexdigest()
        and isinstance(expected_bytes, int)
        and not isinstance(expected_bytes, bool)
        and expected_bytes == len(content)
    )
