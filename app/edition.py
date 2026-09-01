from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import tempfile
import time
from collections import Counter
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.identity import records_are_aliases, source_priority
from app.locking import site_writer_lock
from app.models import Candidate
from app.relevance import classify
from app.sources.gobmx import sanitize_gobmx_title
from app.sources.worldbank import canonical_worldbank_url
from app.state import load_committed_state
from app.taxonomy import enrich
from app.text import words

SCHEMA_VERSION = 8
MAX_SIGNALS = 7
WHY_MIN_WORDS = 12
WHY_MAX_WORDS = 32
TEASER_MAX_WORDS = 70
SUMMARY_MIN_WORDS = 300
SUMMARY_MAX_WORDS = 1000
DEFAULT_TIMEZONE = "America/Mexico_City"
STATIC_ASSET_VERSION = "20260813g"

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
    "source_id",
    "title",
    "summary_teaser",
    "official_title",
    "editorial_status",
    "extraction_status",
    "ai_generated",
    "review_reason",
    "source",
    "url",
    "canonical_url",
    "detail_url",
    "detail_data_url",
    "published_at",
    "official_published_at",
    "detected_at",
    "issuing_body",
    "categories",
    "importance",
    "relevance_score",
    "jurisdiction",
    "country_or_org",
)

PUBLIC_INDEX_FIELDS = PUBLIC_SIGNAL_FIELDS + (
    "content_hash",
    "document_type",
)

ARCHIVE_ITEM_FIELDS = PUBLIC_SIGNAL_FIELDS + (
    "content_hash",
    "first_seen_at",
    "last_seen_at",
    "document_type",
    "case_number",
    "case_parties",
    "case_status",
    "case_treaty",
    "case_outcome",
    "case_reasoning",
    "case_amount",
)

# Lista blanca del registro permanente público. La extracción íntegra y sus
# diagnósticos sólo viven en SQLite, nunca en Git ni en el sitio.
PUBLIC_PERMANENT_FIELDS = ARCHIVE_ITEM_FIELDS + (
    # Compatibilidad del envelope v8: nunca contiene el cuerpo oficial; la
    # proyección sustituye la descripción privada por una cadena vacía.
    "description",
    "summary",
    "card_body",
    "review_reason",
    "official_identifiers",
    "official_evidence",
    "authority",
    "government_branch",
    "published_year",
    "published_month",
    "published_day",
    "topic_tags",
    "subtopic_tags",
    "case_facts",
    "case_claim",
    "executive_summary",
    "detailed_summary",
    "impacts",
    "recommended_actions",
    "evidence",
    "coverage",
)

EDITORIAL_FIELDS = (
    "title",
    "summary_teaser",
    "summary",
    "card_body",
    "detail_markdown",
    "case_facts",
    "case_claim",
    "case_outcome",
    "case_reasoning",
    "case_amount",
    "importance",
    "extraction_status",
    "detail_data_url",
    "executive_summary",
    "detailed_summary",
    "impacts",
    "recommended_actions",
    "evidence",
    "coverage",
)


class PublishError(RuntimeError):
    """El corte no pudo validarse o confirmarse; los artefactos previos siguen vigentes."""


def edition_date_from_generated_at(
    generated_at: Any,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> date:
    """Obtiene la fecha editorial del corte en la zona horaria del producto."""
    try:
        local_tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
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
    """Migra un payload extractivo/v7 al contrato público v8.

    Los campos editoriales de datos legados no se consideran evidencia de una
    revisión completa. Sólo se preserva `complete` cuando el propio registro
    ya declara v8; la reconciliación por `content_hash` ocurre al publicar.
    """
    result = deepcopy(payload)
    pending_state = result.pop("_pending_state", None)
    historical_items = result.pop("_historical_items", None)
    result.pop("cut_id", None)
    result["schema_version"] = SCHEMA_VERSION
    if not isinstance(result.get("collection_notes"), dict):
        result["collection_notes"] = {"dof_previous_day_reviewed": False}
    generated_at = _iso_datetime(result.get("generated_at"))
    result["generated_at"] = generated_at
    result["items"] = [
        _normalize_item(item, generated_at)
        for item in result.get("items", [])
        if isinstance(item, dict)
    ]
    result["total_items"] = len(result["items"])
    result.setdefault("lookback_days", 31)
    result.setdefault("sources", [])
    if force or not isinstance(result.get("edition"), dict):
        edition_date = edition_date_from_generated_at(generated_at, timezone_name)
        result["edition"] = build_default_edition(result, edition_date, timezone_name)
    if pending_state is not None:
        result["_pending_state"] = pending_state
    if isinstance(historical_items, list):
        result["_historical_items"] = [
            _normalize_item(item, generated_at)
            for item in historical_items
            if isinstance(item, dict)
        ]
    return result


def detection_date(
    item: dict[str, Any], timezone_name: str = DEFAULT_TIMEZONE
) -> str | None:
    """Devuelve la fecha local en que el radar detectó el registro.

    Un cambio de estatus debe aparecer en el corte actual sin falsear la fecha
    oficial histórica del asunto. Los registros v7 sin ``detected_at`` usan la
    fecha oficial como fallback de compatibilidad.
    """
    raw = item.get("detected_at")
    if isinstance(raw, str):
        try:
            instant = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            instant = None
        if instant is not None:
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=UTC)
            try:
                return instant.astimezone(ZoneInfo(timezone_name)).date().isoformat()
            except ZoneInfoNotFoundError:
                return instant.date().isoformat()
    official = item.get("official_published_at") or item.get("published_at")
    return official[:10] if isinstance(official, str) else None


def build_default_edition(
    payload: dict[str, Any],
    edition_date: date,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    items = [item for item in payload.get("items", []) if isinstance(item, dict)]
    edition_iso = edition_date.isoformat()
    today_items = [
        item
        for item in items
        if detection_date(item, timezone_name) == edition_iso
    ]
    sources = [source for source in payload.get("sources", []) if isinstance(source, dict)]
    failed = [
        str(source.get("source", "Fuente desconocida"))
        for source in sources
        if source.get("status") in {"error", "degraded"}
    ]
    ok_count = sum(source.get("status") == "ok" for source in sources)

    selected = _select_diverse_signals(today_items)
    digest_reasons = _digest_reason_map(payload.get("digest"))
    signal_refs = [
        {
            "id": item["id"],
            "rank": rank,
            "why_it_matters": _bounded_reason(
                digest_reasons.get(item["id"])
                or item.get("summary_teaser")
                or item.get("review_reason")
                or ""
            ),
        }
        for rank, item in enumerate(selected, start=1)
    ]

    dates = sorted(
        {
            item.get("official_published_at") or item.get("published_at")
            for item in items
            if isinstance(item.get("official_published_at") or item.get("published_at"), str)
        },
        reverse=True,
    )
    return {
        "edition_date": edition_iso,
        "state": "ready" if today_items else "empty",
        "coverage": {
            "state": "degraded" if failed else "complete",
            "ok": ok_count,
            "total": len(sources),
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
            field: deepcopy(item.get(field)) for field in PUBLIC_SIGNAL_FIELDS if field in item
        }
        signal["rank"] = reference.get("rank")
        signal["why_it_matters"] = reference.get("why_it_matters", "")
        signals.append(signal)

    coverage = edition.get("coverage") if isinstance(edition.get("coverage"), dict) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "cut_id": payload.get("cut_id"),
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
    *,
    complete_only: bool = True,
    details_dir: Path | None = None,
) -> dict[str, Any]:
    """Serializa todo el read-modify-write de un corte público."""

    normalized_publications = Path(publications_path)
    data_dir = normalized_publications.parent
    site_root = data_dir.parent if data_dir.name == "data" else data_dir
    with site_writer_lock(site_root):
        return _write_site_artifacts_locked(
            payload,
            normalized_publications,
            edition_path,
            complete_only=complete_only,
            details_dir=details_dir,
        )


def _write_site_artifacts_locked(
    payload: dict[str, Any],
    publications_path: Path,
    edition_path: Path | None = None,
    *,
    complete_only: bool = True,
    details_dir: Path | None = None,
) -> dict[str, Any]:
    """Construye y confirma un corte v8 completo mediante staging y rollback.

    La validación sucede antes del primer reemplazo. Los snapshots propuestos
    viajan en `_pending_state` y sólo se escriben en la misma confirmación que
    edición, compatibilidad, manifiesto, archivo, ítems y fichas estáticas.
    """
    publications_path = Path(publications_path)
    edition_path = Path(edition_path) if edition_path else publications_path.with_name(
        "edition.json"
    )
    data_dir = publications_path.parent
    site_root = data_dir.parent if data_dir.name == "data" else data_dir
    data_prefix = data_dir.relative_to(site_root)
    pending_state = deepcopy(payload.get("_pending_state", {}))
    preserve_edition = payload.get("_preserve_edition") is True

    previous_items = _load_permanent_items(data_dir, publications_path)
    current = prepare_payload(payload)
    current.pop("_pending_state", None)
    historical_transport = current.pop("_historical_items", [])
    current.pop("_preserve_edition", None)
    allow_legacy_editorial_reconciliation = (
        current.pop("_allow_legacy_editorial_reconciliation", False) is True
    )
    baseline_inventory = current.pop("_baseline_inventory", False) is True
    current.pop("_explicit_detection_ids", None)
    state_payloads = _state_payloads(data_dir, pending_state)
    unsafe_state_names = [
        name
        for name in state_payloads
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_-]+", name)
    ]
    if unsafe_state_names:
        raise PublishError("nombre de estado inseguro")
    retired_item_ids = _effective_retired_item_ids(
        current.pop("_retired_item_ids", ()), state_payloads
    )
    previous_by_case = _case_identity_index(previous_items)
    reconciled_current_items = [
        _reconcile_item(
            item, _previous_item(item, previous_items, previous_by_case),
            current["generated_at"],
            preserve_detection=not baseline_inventory,
            reuse_previous_editorial=(
                not complete_only or allow_legacy_editorial_reconciliation
            ),
        )
        for item in current["items"]
        if str(item.get("id") or "") not in retired_item_ids
    ]
    if complete_only:
        current_items = [
            item for item in reconciled_current_items if _is_public_complete(item)
        ]
    else:
        current_items = reconciled_current_items
    historical_items = [
        _reconcile_item(
            item,
            _previous_item(item, previous_items, previous_by_case),
            current["generated_at"],
        )
        for item in historical_transport
        if isinstance(item, dict)
        and str(item.get("id") or "") not in retired_item_ids
    ]
    if complete_only:
        historical_items = [item for item in historical_items if _is_public_complete(item)]
    current["items"] = current_items
    current["total_items"] = len(current_items)
    if not preserve_edition:
        current["edition"] = build_default_edition(
            current, edition_date_from_generated_at(current["generated_at"])
        )

    permanent_items = dict(previous_items)
    superseded_from_invalidations: set[str] = set()
    if complete_only:
        # Resolver invalidaciones contra el inventario previo completo antes
        # de filtrar: aun una ficha legacy que ya no normalice como publicable
        # debe retirarse físicamente si reaparece pendiente/cambiada.
        for item in reconciled_current_items:
            if _is_public_complete(item):
                continue
            incoming_id = str(item.get("id") or "")
            if incoming_id in permanent_items:
                superseded_from_invalidations.add(incoming_id)
            superseded_from_invalidations.update(
                item_id
                for item_id, previous in permanent_items.items()
                if records_are_aliases(item, previous)
            )
        permanent_items = {
            item_id: item
            for item_id, item in permanent_items.items()
            if _is_public_complete(item)
        }
            # Un registro observado de nuevo pero ya no aprobable invalida la
            # versión pública previa y la agenda para retiro transaccional.
        for item in reconciled_current_items:
            if _is_public_complete(item):
                continue
            incoming_id = str(item.get("id") or "")
            if permanent_items.pop(incoming_id, None) is not None:
                superseded_from_invalidations.add(incoming_id)
            for item_id, previous in tuple(permanent_items.items()):
                if records_are_aliases(item, previous):
                    permanent_items.pop(item_id, None)
                    superseded_from_invalidations.add(item_id)
    superseded_item_ids: set[str] = {
        *superseded_from_invalidations,
        *_consolidate_identity_aliases(permanent_items),
    }
    for item_id in retired_item_ids:
        if permanent_items.pop(item_id, None) is not None:
            superseded_item_ids.add(item_id)
    for item in (*historical_items, *current_items):
        superseded_item_ids.update(_remove_legacy_case_aliases(permanent_items, item))
        superseded_item_ids.update(_remove_identity_aliases(permanent_items, item))
        permanent_items[item["id"]] = item
    if complete_only:
        permanent_items = {
            item_id: item
            for item_id, item in permanent_items.items()
            if _is_public_complete(item)
        }

    details_prefix = _details_prefix(details_dir, site_root, data_prefix)
    for item in permanent_items.values():
        if _has_structured_detail(item):
            item["detail_data_url"] = (
                details_prefix / f"{item_key(str(item['id']))}.json"
            ).as_posix()
    if complete_only:
        from app.validation import validate_structured_public_item

        public_errors = [
            error
            for item in permanent_items.values()
            for error in validate_structured_public_item(item)
        ]
        if public_errors:
            raise PublishError("ficha pública inválida: " + "; ".join(public_errors[:8]))
    public_current = deepcopy(current)
    public_current["items"] = [_public_index_item(item) for item in current_items]
    public_current["total_items"] = len(public_current["items"])
    cut_id = compute_cut_id(
        public_current,
        (
            _public_permanent_item(item, legacy=not complete_only)
            for item in permanent_items.values()
        ),
        state_payloads,
    )
    current["cut_id"] = cut_id
    public_current["cut_id"] = cut_id

    from app.validation import validate_publications_payload

    report = validate_publications_payload(current)
    if not report.ok:
        raise PublishError("corte inválido: " + "; ".join(report.errors[:8]))

    artifacts: dict[Path, bytes] = {}
    publications_rel = publications_path.relative_to(site_root)
    edition_rel = edition_path.relative_to(site_root)
    artifacts[publications_rel] = _json_bytes(public_current)
    artifacts[edition_rel] = _json_bytes(build_edition_artifact(current))

    archive_months: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in permanent_items.values():
        month = str(item.get("official_published_at") or item.get("published_at") or "")[:7]
        if len(month) == 7:
            grouped.setdefault(month, []).append(item)
        key = item_key(str(item["id"]))
        public_item = _public_permanent_item(item, legacy=not complete_only)
        artifacts[data_prefix / "items" / f"{key}.json"] = _json_bytes(
            {"schema_version": SCHEMA_VERSION, "cut_id": cut_id, "item": public_item}
        )
        if _has_structured_detail(item):
            artifacts[details_prefix / f"{key}.json"] = _json_bytes(
                _structured_detail_envelope(item, cut_id)
            )
        artifacts[Path("notas") / f"{key}.html"] = _detail_html(item).encode("utf-8")

    for month, month_items in sorted(grouped.items()):
        archive_months.append(month)
        entries = [
            {field: deepcopy(item.get(field)) for field in ARCHIVE_ITEM_FIELDS if field in item}
            for item in sorted(
                month_items,
                key=lambda item: (
                    str(item.get("official_published_at", "")),
                    str(item.get("id", "")),
                ),
                reverse=True,
            )
        ]
        artifacts[data_prefix / "archive" / f"{month}.json"] = _json_bytes(
            {
                "schema_version": SCHEMA_VERSION,
                "cut_id": cut_id,
                "month": month,
                "total_items": len(entries),
                "items": entries,
            }
        )

    for state_name, state in sorted(state_payloads.items()):
        artifacts[data_prefix / "state" / f"{state_name}.json"] = _json_bytes(
            {
                "schema_version": SCHEMA_VERSION,
                "cut_id": cut_id,
                "source": state_name,
                "state": state,
            }
        )

    artifact_index = {
        path.as_posix(): {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
        for path, content in sorted(artifacts.items(), key=lambda pair: pair[0].as_posix())
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "publication_policy": "complete-only" if complete_only else "legacy-compatible",
        "cut_id": cut_id,
        "generated_at": current["generated_at"],
        "edition_date": current["edition"]["edition_date"],
        "archive_months": archive_months,
        "artifact_count": len(artifact_index),
        "artifacts": artifact_index,
    }
    manifest_rel = data_dir.joinpath("manifest.json").relative_to(site_root)
    artifacts[manifest_rel] = _json_bytes(manifest)
    retired = tuple(
        sorted(
            {
                *_retired_artifacts(
                    data_dir / "manifest.json",
                    keep={path.as_posix() for path in artifacts},
                    data_prefix=data_prefix,
                ),
                *_retired_item_artifacts(
                    data_dir / "manifest.json",
                    item_ids=superseded_item_ids,
                    data_prefix=data_prefix,
                    details_prefix=details_prefix,
                ),
            },
            key=lambda path: path.as_posix(),
        )
    )

    site_root.mkdir(parents=True, exist_ok=True)
    _cleanup_abandoned_staging(site_root)
    stage_root = Path(tempfile.mkdtemp(prefix=".radar-stage-", dir=site_root))
    try:
        (stage_root / ".radar-stage-owner.json").write_text(
            json.dumps({"pid": os.getpid(), "created_at": time.time()}),
            encoding="utf-8",
        )
        for relative, content in artifacts.items():
            if relative.is_absolute() or ".." in relative.parts:
                raise PublishError(f"ruta de artefacto insegura: {relative}")
            staged = stage_root / relative
            try:
                staged.resolve().relative_to(stage_root.resolve())
            except ValueError as exc:
                raise PublishError(f"ruta de artefacto fuera de staging: {relative}") from exc
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(content)

        from app.validation import validate_site_artifacts

        staged_report = validate_site_artifacts(
            stage_root / manifest_rel,
            root=stage_root,
            require_complete_only=complete_only,
        )
        if not staged_report.ok:
            raise PublishError("staging inválido: " + "; ".join(staged_report.errors[:8]))
        # `manifest.json` is the commit marker. It must be the final replace:
        # readers reject any state bytes left ahead of an old manifest after
        # an uncatchable process termination.
        commit_paths = tuple(path for path in artifacts if path != manifest_rel) + (
            manifest_rel,
        )
        _commit_staged_artifacts(
            stage_root,
            site_root,
            commit_paths,
            retire=retired,
        )
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
    return manifest


def write_site_artifacts_legacy(
    payload: dict[str, Any],
    publications_path: Path,
    edition_path: Path | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Compatibilidad explícita para fixtures/migraciones v7, nunca automatización v8."""
    options.setdefault("complete_only", False)
    return write_site_artifacts(
        payload, publications_path, edition_path, **options
    )


def _is_public_complete(item: dict[str, Any]) -> bool:
    return (
        item.get("extraction_status") == "complete"
        and item.get("editorial_status") == "complete"
        and item.get("ai_generated") is True
        and bool(item.get("source_content_hash"))
        and item.get("source_revalidation_status") == "complete"
    )


def _has_structured_detail(item: dict[str, Any]) -> bool:
    return all(item.get(field) not in (None, "", [], {}) for field in (
        "executive_summary",
        "detailed_summary",
        "impacts",
        "recommended_actions",
        "evidence",
        "coverage",
    ))


def _public_index_item(item: dict[str, Any]) -> dict[str, Any]:
    """Proyección ligera: el análisis extenso vive sólo en la ficha por hash."""
    return {
        field: deepcopy(item.get(field))
        for field in PUBLIC_INDEX_FIELDS
        if field in item
    }


def _public_permanent_item(
    item: dict[str, Any], *, legacy: bool = False
) -> dict[str, Any]:
    """Proyección exhaustiva sin texto extraído ni diagnósticos privados."""
    result = {
        field: deepcopy(item.get(field))
        for field in PUBLIC_PERMANENT_FIELDS
        if field in item
    }
    # Description puede contener el cuerpo íntegro de fuentes legacy y no
    # forma parte de la identidad v8. Nunca sale de la base privada.
    result["description"] = str(item.get("description") or "") if legacy else ""
    if legacy and "detail_markdown" in item:
        result["detail_markdown"] = deepcopy(item.get("detail_markdown"))
    return result


def _details_prefix(
    details_dir: Path | None, site_root: Path, data_prefix: Path
) -> Path:
    if details_dir is None:
        return data_prefix / "fichas"
    path = Path(details_dir)
    try:
        return path.resolve().relative_to(site_root.resolve())
    except ValueError as exc:
        raise PublishError("details_dir debe quedar dentro del sitio público") from exc


def _structured_detail_envelope(item: dict[str, Any], cut_id: str) -> dict[str, Any]:
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "cut_id": cut_id,
        "item": _public_index_item(item),
        "executive_summary": deepcopy(item["executive_summary"]),
        "detailed_summary": deepcopy(item["detailed_summary"]),
        "impacts": deepcopy(item["impacts"]),
        "recommended_actions": deepcopy(item["recommended_actions"]),
        "evidence": deepcopy(item["evidence"]),
        "coverage": deepcopy(item["coverage"]),
    }
    return envelope


def item_key(publication_id: str) -> str:
    return hashlib.sha256(publication_id.encode("utf-8")).hexdigest()[:24]


def load_manifested_items(publications_path: Path) -> dict[str, dict[str, Any]]:
    """Carga las fichas permanentes que pertenecen al corte confirmado.

    El manifiesto es la autoridad del inventario: no se recorren archivos
    huérfanos de ``data/items``. La validación integral de hashes y referencias
    corresponde a ``validate_site_artifacts``; aquí se exige además que cada
    envelope leído declare el mismo ``cut_id`` antes de exponerlo al canal
    editorial.
    """

    publications_path = Path(publications_path)
    data_dir = publications_path.parent
    site_root = data_dir.parent if data_dir.name == "data" else data_dir
    manifest_path = data_dir / "manifest.json"
    _safe_site_file(site_root, manifest_path)
    if not manifest_path.exists():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublishError(f"no se pudo leer el manifiesto vigente: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("artifacts"), dict):
        raise PublishError("el manifiesto vigente no contiene un índice de artefactos")

    expected_cut_id = manifest.get("cut_id")
    result: dict[str, dict[str, Any]] = {}
    for path in _manifest_item_paths(data_dir):
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PublishError(f"no se pudo leer la ficha permanente {path}: {exc}") from exc
        if not isinstance(envelope, dict) or envelope.get("cut_id") != expected_cut_id:
            raise PublishError(f"la ficha permanente {path} no pertenece al corte vigente")
        item = envelope.get("item")
        item_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(item_id, str) or not item_id:
            raise PublishError(f"la ficha permanente {path} no contiene un id válido")
        if item_id in result:
            raise PublishError(f"id permanente duplicado en el manifiesto: {item_id}")
        result[item_id] = deepcopy(item)
    return result


def _normalize_item(item: dict[str, Any], generated_at: str) -> dict[str, Any]:
    result = deepcopy(item)
    incoming_content_hash = str(result.get("content_hash") or "").strip()
    incoming_editorial_status = result.get("editorial_status")
    source = str(result.get("source") or "Fuente desconocida").strip()
    source_id = str(result.get("source_id") or "").strip()
    item_id = str(result.get("id") or "").strip()
    if not source_id and ":" in item_id:
        source_id = item_id.split(":", 1)[1]
    url = str(result.get("canonical_url") or result.get("url") or "").strip()
    public_url = str(result.get("url") or url).strip()
    if source == "Banco Mundial":
        url = canonical_worldbank_url(url)
        public_url = canonical_worldbank_url(public_url)
    canonical_url = _canonical_url(url)
    source_id = source_id or canonical_url
    item_id = item_id or f"{source.casefold()}:{source_id}"
    official_date = str(
        result.get("official_published_at") or result.get("published_at") or generated_at[:10]
    )[:10]
    raw_detected_at = result.get("detected_at")
    if raw_detected_at:
        detected_at = _iso_datetime(raw_detected_at)
    else:
        # Un inventario v7 no registra cuándo fue detectado. Usar la hora del
        # bootstrap anunciaría toda la ventana móvil como novedad del día.
        # El mediodía UTC conserva la fecha oficial al convertirla a Ciudad
        # de México y deja explícito que se trata de un fallback histórico.
        detected_at = _iso_datetime(f"{official_date}T12:00:00+00:00")

    result.update(
        {
            "id": item_id,
            "source_id": source_id,
            "source": source,
            "url": public_url or canonical_url,
            "canonical_url": canonical_url,
            "detail_url": f"notas/{item_key(item_id)}.html",
            "detail_data_url": "",
            "official_published_at": official_date,
            "published_at": official_date,
            "detected_at": detected_at,
            "first_seen_at": _iso_datetime(result.get("first_seen_at") or detected_at),
            "last_seen_at": _latest_iso_datetime(
                result.get("last_seen_at") or generated_at,
                result.get("first_seen_at") or detected_at,
            ),
        }
    )
    case_number = _case_number(result)
    if case_number:
        result["case_number"] = case_number
    official_date_value = datetime.fromisoformat(official_date).date()
    defaults: dict[str, Any] = {
        "official_title": str(result.get("title") or item_id),
        "description": "",
        "authority": "",
        "document_type": "",
        "issuing_body": str(result.get("authority") or source),
        "government_branch": "No determinado",
        "jurisdiction": "nacional",
        "country_or_org": "México",
        "published_year": official_date_value.year,
        "published_month": official_date_value.month,
        "published_day": official_date_value.day,
        "categories": [],
        "topic_tags": [],
        "subtopic_tags": [],
        "importance": 1,
        "relevance_score": 0,
        "ai_generated": False,
    }
    for field, default in defaults.items():
        if result.get(field) is None:
            result[field] = default
        else:
            result.setdefault(field, default)
    result.setdefault("official_identifiers", {"source_id": source_id})
    if not isinstance(result["official_identifiers"], dict) or not result[
        "official_identifiers"
    ]:
        result["official_identifiers"] = {"source_id": source_id}
    if case_number:
        result["official_identifiers"].setdefault("case_number", case_number)
    result.setdefault("official_evidence", {"primary_url": result["url"]})
    if not isinstance(result["official_evidence"], dict) or not result["official_evidence"]:
        result["official_evidence"] = {"primary_url": result["url"]}
    if source == "Banco Mundial":
        result["official_evidence"] = {
            key: canonical_worldbank_url(value) if isinstance(value, str) else value
            for key, value in result["official_evidence"].items()
        }

    for field in (
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
        result.setdefault(field, "")
    derived_importance = _refresh_extractive_fields(result)
    # Nunca confiar en un digest aportado por el payload: la huella se deriva
    # de la evidencia oficial normalizada. Así, cambiar el título oficial u
    # otro dato sustantivo sin actualizar el hash no puede conservar una
    # editorial obsoleta.
    projected_public_item = "description" in item and item.get("description") == ""
    derived_content_hash = official_content_hash(result)
    # Los envelopes públicos v8 omiten la descripción privada. Su hash ya fue
    # validado al exportar y debe preservarse al recargar el manifiesto; sólo
    # entradas privadas/de recolector se vuelven a derivar aquí.
    result["content_hash"] = (
        incoming_content_hash
        if projected_public_item and incoming_content_hash
        else derived_content_hash
    )
    result.setdefault("extraction_status", "pending")
    for field, default in (
        ("executive_summary", None),
        ("detailed_summary", None),
        ("impacts", None),
        ("recommended_actions", None),
        ("evidence", None),
        ("coverage", None),
    ):
        result.setdefault(field, default)
    if (
        incoming_editorial_status == "complete"
        and incoming_content_hash != result["content_hash"]
    ):
        result["editorial_status"] = "needs_review"
        result["review_reason"] = (
            "La evidencia oficial cambió durante la normalización; "
            "requiere una nueva revisión editorial."
        )
        result["importance"] = derived_importance

    if result.get("editorial_status") not in {"complete", "needs_review", "pending"}:
        result["editorial_status"] = "needs_review"
    if result["editorial_status"] != "complete":
        for field in ("title", "summary_teaser", "summary", "card_body", "detail_markdown"):
            result[field] = None
        for field in (
            "executive_summary",
            "detailed_summary",
            "impacts",
            "recommended_actions",
            "evidence",
            "coverage",
        ):
            result[field] = None
        # `case_facts` es interpretación editorial heredada del contrato v7;
        # nunca debe sobrevivir una migración a revisión pendiente. Los
        # campos extractivos (litis, tratado, estatus y evidencia) sí se
        # conservan cuando vienen del recolector oficial.
        result["case_facts"] = ""
        result["review_reason"] = str(
            result.get("review_reason")
            or "Registro legado o contenido sin revisión editorial certificada."
        )
        result["ai_generated"] = False
    else:
        result["review_reason"] = None
    return result


def _refresh_extractive_fields(item: dict[str, Any]) -> int:
    """Recalcula taxonomía derivada sólo desde evidencia oficial almacenada."""

    source = str(item.get("source") or "").strip()
    official_title = str(item.get("official_title") or "").strip()
    if source in {"Gob.mx APF", "IMPI"}:
        official_title = sanitize_gobmx_title(official_title)
        item["official_title"] = official_title

    official_date = date.fromisoformat(str(item["official_published_at"])[:10])
    evidence = item.get("official_evidence")
    official_evidence = (
        {
            str(key): value
            for key, value in evidence.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        if isinstance(evidence, dict)
        else {}
    )
    candidate = Candidate(
        source=source,
        source_id=str(item.get("source_id") or ""),
        url=str(item.get("url") or item.get("canonical_url") or ""),
        canonical_url=str(item.get("canonical_url") or ""),
        official_title=official_title,
        description=str(item.get("description") or ""),
        published_at=official_date,
        official_published_at=official_date,
        authority=str(item.get("authority") or ""),
        document_type=str(item.get("document_type") or ""),
        case_number=str(item.get("case_number") or ""),
        case_parties=str(item.get("case_parties") or ""),
        case_status=str(item.get("case_status") or ""),
        case_treaty=str(item.get("case_treaty") or ""),
        case_claim=str(item.get("case_claim") or ""),
        case_outcome=str(item.get("case_outcome") or ""),
        case_reasoning=str(item.get("case_reasoning") or ""),
        case_amount=str(item.get("case_amount") or ""),
        official_evidence=official_evidence,
    )
    classified = classify(candidate)
    taxonomy = enrich(classified)
    item.update(
        {
            "issuing_body": taxonomy.issuing_body,
            "government_branch": taxonomy.government_branch,
            "jurisdiction": taxonomy.jurisdiction,
            "country_or_org": taxonomy.country_or_org,
            "published_year": taxonomy.published_year,
            "published_month": taxonomy.published_month,
            "published_day": taxonomy.published_day,
            "categories": list(taxonomy.primary_categories),
            "topic_tags": list(taxonomy.topic_tags),
            "subtopic_tags": list(taxonomy.subtopic_tags),
            "relevance_score": classified.relevance_score,
        }
    )
    if item.get("editorial_status") != "complete":
        item["importance"] = taxonomy.importance
    return taxonomy.importance


def _reconcile_item(
    incoming: dict[str, Any],
    previous: dict[str, Any] | None,
    generated_at: str,
    *,
    preserve_detection: bool = True,
    reuse_previous_editorial: bool = True,
) -> dict[str, Any]:
    item = deepcopy(incoming)
    if previous is None:
        return item
    previous = _normalize_item(previous, generated_at)
    if (
        str(previous.get("id") or "") != str(item.get("id") or "")
        and records_are_aliases(previous, item)
        and source_priority(str(previous.get("source") or ""))
        > source_priority(str(item.get("source") or ""))
    ):
        # Si hoy sólo respondió el agregador, no degradar la identidad
        # canónica elegida en un corte anterior. La copia específica sigue
        # siendo la ficha pública y se marca como observada en este corte.
        preferred = deepcopy(previous)
        if not preserve_detection:
            preferred["detected_at"] = item["detected_at"]
            preferred["first_seen_at"] = item["first_seen_at"]
        preferred["last_seen_at"] = _latest_iso_datetime(
            generated_at, preferred.get("first_seen_at") or preferred["detected_at"]
        )
        return preferred
    if preserve_detection:
        item["first_seen_at"] = previous.get("first_seen_at", item["first_seen_at"])
    item["last_seen_at"] = _latest_iso_datetime(
        generated_at, item.get("first_seen_at") or item["detected_at"]
    )
    incoming_source_hash = _editorial_source_hash(item)
    previous_source_hash = _editorial_source_hash(previous)
    source_changed = (
        incoming_source_hash != previous_source_hash
        if incoming_source_hash and previous_source_hash
        else item["content_hash"] != previous.get("content_hash")
    )
    reapproved_for_current_source = (
        item.get("editorial_status") == "complete"
        and item.get("ai_generated") is True
        and item.get("extraction_status") == "complete"
        and item.get("source_revalidation_status") == "complete"
        and bool(incoming_source_hash)
        and isinstance(item.get("evidence"), dict)
        and str(item["evidence"].get("content_hash") or "")
        == incoming_source_hash
    )
    if source_changed and not reapproved_for_current_source:
        item["editorial_status"] = "needs_review"
        item["review_reason"] = "La fuente oficial cambió; requiere una nueva revisión editorial."
        for field in ("title", "summary_teaser", "summary", "card_body", "detail_markdown"):
            item[field] = None
        # El incoming ya trae la extracción oficial nueva. Limpiar aquí
        # `case_claim`/tratado/estatus/resultado eliminaría precisamente la
        # evidencia que provocó el cambio de huella; sólo `case_facts` es una
        # interpretación editorial legacy y se normaliza siempre a vacío.
        item["case_facts"] = ""
        item["ai_generated"] = False
        return item

    if preserve_detection:
        item["detected_at"] = previous.get("detected_at", item["detected_at"])
    if reuse_previous_editorial and item.get("editorial_status") != "complete" and previous.get(
        "editorial_status"
    ) == "complete":
        for field in EDITORIAL_FIELDS:
            item[field] = deepcopy(previous.get(field))
        item["editorial_status"] = "complete"
        item["review_reason"] = None
        item["ai_generated"] = True
    return item


def _editorial_source_hash(item: dict[str, Any]) -> str:
    direct = str(item.get("source_content_hash") or "").strip()
    if direct:
        return direct
    evidence = item.get("evidence")
    if isinstance(evidence, dict):
        return str(evidence.get("content_hash") or "").strip()
    return ""


def _load_permanent_items(
    data_dir: Path, publications_path: Path
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    site_root = data_dir.parent if data_dir.name == "data" else data_dir
    # Los índices ligeros sirven únicamente como fallback de migraciones que
    # todavía no cuenten con envelopes de detalle.
    for path in sorted(data_dir.joinpath("archive").glob("????-??.json")):
        _safe_site_file(site_root, path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        generated_at = _iso_datetime(payload.get("generated_at"))
        for item in payload.get("items", []):
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                result[item["id"]] = _normalize_item(item, generated_at)

    # Los envelopes de detalle son la fuente histórica primaria: contienen
    # evidencia y editorial completas aunque el índice público sea ligero.
    for path in _manifest_item_paths(data_dir):
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        item = envelope.get("item") if isinstance(envelope, dict) else None
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            generated_at = _iso_datetime(item.get("last_seen_at"))
            result[item["id"]] = _normalize_item(item, generated_at)

    if publications_path.exists():
        _safe_site_file(site_root, publications_path)
        try:
            payload = json.loads(publications_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            generated_at = _iso_datetime(payload.get("generated_at"))
            for item in payload.get("items", []):
                if (
                    isinstance(item, dict)
                    and isinstance(item.get("id"), str)
                    and item["id"] not in result
                ):
                    result[item["id"]] = _normalize_item(item, generated_at)
    return result


def _manifest_item_paths(data_dir: Path) -> tuple[Path, ...]:
    """Carga sólo envelopes que pertenecen al último corte confirmado.

    Antes de v8 no existía manifiesto; en ese caso se conserva el glob como
    fallback de migración. Una vez que hay manifiesto, ignorar archivos
    huérfanos evita resucitar ids legacy sustituidos en corridas posteriores.
    """

    site_root = data_dir.parent if data_dir.name == "data" else data_dir
    manifest_path = data_dir / "manifest.json"
    _safe_site_file(site_root, manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        paths = tuple(sorted(data_dir.joinpath("items").glob("*.json")))
        for path in paths:
            _safe_site_file(site_root, path)
        return paths
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if not isinstance(artifacts, dict):
        paths = tuple(sorted(data_dir.joinpath("items").glob("*.json")))
        for path in paths:
            _safe_site_file(site_root, path)
        return paths
    prefix = data_dir.relative_to(site_root) / "items"
    paths: list[Path] = []
    for relative_text in artifacts:
        if not isinstance(relative_text, str):
            continue
        relative = Path(relative_text)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.parent != prefix
            or relative.suffix != ".json"
        ):
            continue
        candidate = site_root / relative
        _safe_site_file(site_root, candidate)
        paths.append(candidate)
    return tuple(sorted(paths))


def _safe_site_file(site_root: Path, candidate: Path) -> Path:
    """Reject direct/intermediate symlinks before a prior public cut is read."""

    root = Path(site_root)
    if root.is_symlink():
        raise PublishError("la raíz pública no puede ser un enlace simbólico")
    try:
        relative = Path(candidate).relative_to(root)
    except ValueError as exc:
        raise PublishError("ruta pública fuera del sitio") from exc
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise PublishError(f"artefacto público no puede ser un enlace: {relative}")
    try:
        Path(candidate).resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise PublishError(f"artefacto público fuera del sitio: {relative}") from exc
    return Path(candidate)


def _previous_item(
    incoming: dict[str, Any],
    previous_items: dict[str, dict[str, Any]],
    previous_by_case: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any] | None:
    exact = previous_items.get(str(incoming.get("id") or ""))
    if exact is not None:
        return exact
    case_alias = previous_by_case.get(_case_identity(incoming))
    if case_alias is not None:
        return case_alias
    aliases = [
        previous
        for previous in previous_items.values()
        if records_are_aliases(incoming, previous)
    ]
    return max(aliases, key=_identity_preference) if aliases else None


def _identity_preference(item: dict[str, Any]) -> tuple[int, int, str]:
    return (
        source_priority(str(item.get("source") or "")),
        len(str(item.get("description") or "")),
        str(item.get("id") or ""),
    )


def _consolidate_identity_aliases(
    permanent_items: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    """Colapsa todos los aliases ya archivados, incluidos los no vistos hoy."""

    remaining = set(permanent_items)
    removed: list[str] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        component = {seed}
        frontier = [seed]
        while frontier:
            current_id = frontier.pop()
            current = permanent_items[current_id]
            aliases = {
                candidate_id
                for candidate_id in remaining
                if records_are_aliases(current, permanent_items[candidate_id])
            }
            if aliases:
                component.update(aliases)
                remaining.difference_update(aliases)
                frontier.extend(sorted(aliases))

        if len(component) == 1:
            continue
        winner_id = max(
            component,
            key=lambda item_id: _identity_preference(permanent_items[item_id]),
        )
        winner = deepcopy(permanent_items[winner_id])
        members = [permanent_items[item_id] for item_id in component]
        winner["first_seen_at"] = _temporal_extreme(
            (member.get("first_seen_at") for member in members),
            fallback=str(winner.get("first_seen_at") or ""),
            latest=False,
        )
        winner["detected_at"] = _temporal_extreme(
            (member.get("detected_at") for member in members),
            fallback=str(winner.get("detected_at") or ""),
            latest=False,
        )
        winner["last_seen_at"] = _temporal_extreme(
            (member.get("last_seen_at") for member in members),
            fallback=str(winner.get("last_seen_at") or ""),
            latest=True,
        )
        permanent_items[winner_id] = winner
        for item_id in sorted(component - {winner_id}):
            permanent_items.pop(item_id, None)
            removed.append(item_id)
    return tuple(removed)


def _temporal_extreme(values: Any, *, fallback: str, latest: bool) -> str:
    parsed: list[tuple[datetime, str]] = []
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)
        parsed.append((instant.astimezone(UTC), instant.isoformat()))
    if not parsed:
        return fallback
    selected = max(parsed) if latest else min(parsed)
    return selected[1]


def _remove_identity_aliases(
    permanent_items: dict[str, dict[str, Any]], incoming: dict[str, Any]
) -> tuple[str, ...]:
    """Consolida aliases deterministas dentro de la misma transacción."""

    incoming_id = str(incoming.get("id") or "")
    removed: list[str] = []
    for item_id, previous in tuple(permanent_items.items()):
        if item_id != incoming_id and records_are_aliases(incoming, previous):
            permanent_items.pop(item_id, None)
            removed.append(item_id)
    return tuple(removed)


def _case_identity_index(
    items: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items.values():
        identity = _case_identity(item)
        if identity != ("", ""):
            result[identity] = item
    return result


def _remove_legacy_case_aliases(
    permanent_items: dict[str, dict[str, Any]], incoming: dict[str, Any]
) -> tuple[str, ...]:
    identity = _case_identity(incoming)
    if identity == ("", ""):
        return ()
    incoming_id = str(incoming.get("id") or "")
    removed: list[str] = []
    for item_id, previous in tuple(permanent_items.items()):
        if item_id != incoming_id and _case_identity(previous) == identity:
            permanent_items.pop(item_id, None)
            removed.append(item_id)
    return tuple(removed)


def _case_identity(item: dict[str, Any]) -> tuple[str, str]:
    source = str(item.get("source") or "").strip().casefold()
    if source not in {"ciadi", "secretariado t-mec"}:
        return "", ""
    case_number = _case_number(item)
    return (source, case_number.casefold()) if case_number else ("", "")


def _case_number(item: dict[str, Any]) -> str:
    direct = str(item.get("case_number") or "").strip()
    if direct:
        return direct
    identifiers = item.get("official_identifiers")
    if isinstance(identifiers, dict):
        identified = str(identifiers.get("case_number") or "").strip()
        if identified:
            return identified
    source = str(item.get("source") or "").strip().casefold()
    url = str(item.get("canonical_url") or item.get("url") or "")
    if source == "ciadi":
        query = parse_qs(urlsplit(url).query)
        for key, values in query.items():
            if key.casefold() == "caseno" and values:
                return values[0].strip()
        haystack = " ".join(
            str(item.get(field) or "") for field in ("official_title", "title", "description")
        )
        match = re.search(
            r"\b(?:(?:ARB|CONC)(?:\(AF\))?|UNCT)/\d{2,4}/\d+\b",
            haystack,
        )
        return match.group(0) if match else ""
    if source == "secretariado t-mec":
        haystack = " ".join(
            str(item.get(field) or "") for field in ("official_title", "title", "description")
        )
        match = re.search(r"\b[A-Z]{3}-[A-Z]{3}-\d{4}-[A-Z0-9.-]+\b", haystack)
        return match.group(0) if match else ""
    return ""


def _state_payloads(data_dir: Path, pending: Any) -> dict[str, Any]:
    states: dict[str, Any] = {}
    state_dir = data_dir / "state"
    for path in state_dir.glob("*.json") if state_dir.exists() else ():
        payload = load_committed_state(path, source=path.stem)
        if payload is not None:
            states[path.stem] = _public_state_payload(path.stem, payload)
    legacy = {"icsid": data_dir / "icsid_snapshot.json", "tmec": data_dir / "tmec_snapshot.json"}
    for name, path in legacy.items():
        if name in states or not path.exists():
            continue
        payload = load_committed_state(path, source=name)
        if payload is not None:
            states[name] = _public_state_payload(name, payload)
    if isinstance(pending, dict):
        states.update(
            {
                name: _public_state_payload(name, payload)
                for name, payload in pending.items()
            }
        )
    return states


def _public_state_payload(name: str, payload: Any) -> Any:
    """Keep legacy state compatible while stripping retraction rollback data.

    A historical v8 retractions envelope may contain the complete withdrawn
    record. It is no longer a public representation: exporting any new cut
    converts it to an opaque tombstone before it participates in the cut hash.
    """

    if name != "retractions" or not isinstance(payload, dict):
        return payload
    raw_items = payload.get("items")
    tombstones: dict[str, dict[str, str]] = {}
    if isinstance(raw_items, dict):
        for item_id, entry in raw_items.items():
            if not isinstance(item_id, str) or not isinstance(entry, dict):
                continue
            retracted_at = entry.get("retracted_at")
            if isinstance(retracted_at, str) and retracted_at:
                tombstones[item_id] = {"retracted_at": retracted_at}
    return {"version": 2, "items": tombstones}


def _effective_retired_item_ids(requested: Any, states: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    if isinstance(requested, (list, tuple, set)):
        result.update(
            item_id.strip()
            for item_id in requested
            if isinstance(item_id, str) and item_id.strip()
        )
    ledger = states.get("retractions")
    entries = ledger.get("items") if isinstance(ledger, dict) else None
    if isinstance(entries, dict):
        result.update(
            item_id.strip()
            for item_id in entries
            if isinstance(item_id, str) and item_id.strip()
        )
    return result


def _commit_staged_artifacts(
    stage_root: Path,
    site_root: Path,
    paths: tuple[Path, ...],
    *,
    retire: tuple[Path, ...] = (),
) -> None:
    if not paths:
        return
    backup_root = stage_root / ".backup"
    committed: list[tuple[Path, Path | None]] = []
    try:
        # El último path es el manifiesto/commit marker: se reemplaza sólo
        # después de escribir lo nuevo y retirar los artefactos invalidados.
        # Si cualquier paso falla, el backup restaura también los retiros.
        for relative in paths[:-1]:
            staged = stage_root / relative
            target = site_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            backup: Path | None = None
            if target.exists():
                backup = backup_root / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, backup)
            try:
                os.replace(staged, target)
            except Exception:
                if backup is not None and backup.exists():
                    os.replace(backup, target)
                raise
            committed.append((target, backup))
        for relative in retire:
            target = site_root / relative
            if not target.exists():
                continue
            backup = backup_root / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, backup)
            committed.append((target, backup))
        relative = paths[-1]
        staged = stage_root / relative
        target = site_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if target.exists():
            backup = backup_root / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, backup)
        try:
            os.replace(staged, target)
        except Exception:
            if backup is not None and backup.exists():
                os.replace(backup, target)
            raise
        committed.append((target, backup))
    except Exception:
        for target, backup in reversed(committed):
            if target.exists():
                target.unlink()
            if backup is not None and backup.exists():
                os.replace(backup, target)
        raise


def _cleanup_abandoned_staging(site_root: Path) -> None:
    """Remove only staging directories whose writer is certainly gone.

    Markerless directories are retained for a day to avoid racing a writer
    between `mkdtemp` and marker creation. A symlink is never followed.
    """

    for stage_root in site_root.glob(".radar-stage-*"):
        if not stage_root.is_dir() or stage_root.is_symlink():
            continue
        marker = stage_root / ".radar-stage-owner.json"
        try:
            owner = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            try:
                old_enough = time.time() - stage_root.stat().st_mtime > 86_400
            except OSError:
                continue
            if old_enough:
                shutil.rmtree(stage_root, ignore_errors=True)
            continue
        pid = owner.get("pid") if isinstance(owner, dict) else None
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            shutil.rmtree(stage_root, ignore_errors=True)
        except (PermissionError, OSError):
            continue


def _retired_artifacts(
    previous_manifest_path: Path,
    *,
    keep: set[str],
    data_prefix: Path,
) -> tuple[Path, ...]:
    """Retira índices/estado reemplazables que ya no pertenecen al corte.

    Las rutas se toman exclusivamente del manifiesto anterior, se validan
    como relativas y se mueven al backup de la misma transacción. Item, ficha
    y nota se agregan por id invalidado en ``write_site_artifacts``.
    """
    try:
        previous = json.loads(previous_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    previous_artifacts = previous.get("artifacts") if isinstance(previous, dict) else None
    if not isinstance(previous_artifacts, dict):
        return ()
    allowed_prefixes = (
        (data_prefix / "archive").as_posix().rstrip("/") + "/",
        (data_prefix / "state").as_posix().rstrip("/") + "/",
    )
    retired: list[Path] = []
    for path_text in previous_artifacts:
        if not isinstance(path_text, str) or path_text in keep:
            continue
        relative = Path(path_text)
        if relative.is_absolute() or ".." in relative.parts:
            continue
        if path_text.startswith(allowed_prefixes):
            retired.append(relative)
    return tuple(sorted(retired, key=lambda path: path.as_posix()))


def _retired_item_artifacts(
    previous_manifest_path: Path,
    *,
    item_ids: set[str],
    data_prefix: Path,
    details_prefix: Path,
) -> tuple[Path, ...]:
    """Resuelve sólo item/ficha/nota declarados por el manifiesto anterior."""
    if not item_ids:
        return ()
    try:
        previous = json.loads(previous_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    declared = previous.get("artifacts") if isinstance(previous, dict) else None
    if not isinstance(declared, dict):
        return ()
    candidates = {
        path
        for item_id in item_ids
        for path in (
            data_prefix / "items" / f"{item_key(item_id)}.json",
            details_prefix / f"{item_key(item_id)}.json",
            Path("notas") / f"{item_key(item_id)}.html",
        )
    }
    return tuple(
        sorted(
            (
                path
                for path in candidates
                if path.as_posix() in declared
                and not path.is_absolute()
                and ".." not in path.parts
            ),
            key=lambda path: path.as_posix(),
        )
    )


def compute_cut_id(
    publications: dict[str, Any],
    permanent_items: Any,
    state: dict[str, Any],
) -> str:
    """Calcula la identidad reproducible de todo el corte transaccional."""

    stable_publications = deepcopy(publications)
    stable_publications.pop("cut_id", None)
    ordered_items = sorted(
        (deepcopy(item) for item in permanent_items if isinstance(item, dict)),
        key=lambda item: str(item.get("id") or ""),
    )
    stable = {
        "publications": stable_publications,
        "permanent_items": ordered_items,
        "state": deepcopy(state),
    }
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()[:24]


def official_content_hash(item: dict[str, Any]) -> str:
    """Deriva la huella de evidencia oficial, excluyendo interpretación editorial."""

    official = {
        field: item.get(field)
        for field in (
            "source",
            "source_id",
            "canonical_url",
            "official_title",
            "official_published_at",
            "authority",
            "document_type",
            "case_number",
            "case_parties",
            "case_status",
            "case_treaty",
            "official_evidence",
        )
    }
    encoded = json.dumps(
        official, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def _iso_datetime(value: Any) -> str:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.isoformat()
    return datetime.now(UTC).isoformat()


def _latest_iso_datetime(value: Any, floor: Any) -> str:
    normalized = _iso_datetime(value)
    normalized_floor = _iso_datetime(floor)
    observed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    minimum = datetime.fromisoformat(normalized_floor.replace("Z", "+00:00"))
    return normalized if observed >= minimum else normalized_floor


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _detail_html(item: dict[str, Any]) -> str:
    title = item.get("title") or item.get("official_title") or "Publicación oficial"
    description = _metadata_description(
        item.get("summary_teaser") or item.get("description") or item.get("review_reason")
    )
    status = (
        "Completa" if item.get("editorial_status") == "complete" else "Revisión pendiente"
    )
    summary = item.get("summary") if item.get("editorial_status") == "complete" else None
    evidence = (
        item.get("official_evidence") if isinstance(item.get("official_evidence"), dict) else {}
    )
    evidence_links = "".join(
        f'<li><a href="{html.escape(str(url), quote=True)}">'
        f"{html.escape(_human_label(str(label)))}</a></li>"
        for label, url in evidence.items()
        if isinstance(url, str) and url.startswith(("http://", "https://"))
    )
    source_url = html.escape(
        str(item.get("canonical_url") or item.get("url") or ""), quote=True
    )
    body = (
        '<section aria-labelledby="resumen"><h2 id="resumen">Resumen</h2>'
        f"<p>{html.escape(str(summary))}</p></section>"
        + _structured_card_html(item.get("card_body"))
        if summary
        else '<p class="review-note">'
        + html.escape(str(item.get("review_reason") or "Pendiente de revisión editorial."))
        + "</p>"
    )
    identifiers = _definition_html(
        item.get("official_identifiers"), section_id="identificadores", title="Identificadores"
    )
    case_fields = {
        "Número de caso": item.get("case_number"),
        "Partes": item.get("case_parties"),
        "Litis": item.get("case_claim"),
        "Tratado": item.get("case_treaty"),
        "Estatus": item.get("case_status"),
        "Resultado": item.get("case_outcome"),
        "Razonamiento": item.get("case_reasoning"),
        "Monto": item.get("case_amount"),
    }
    case_html = _definition_html(case_fields, section_id="caso", title="Datos del caso")
    note_url = (
        "https://bpop06.github.io/radar-regulatorio-mx/"
        + str(item.get("detail_url") or f"notas/{item_key(str(item.get('id', '')))}.html")
    )
    canonical = html.escape(note_url, quote=True)
    json_ld = _article_json_ld(
        item,
        title=str(title),
        description=str(description or ""),
        canonical_url=note_url,
        source_url=str(item.get("canonical_url") or item.get("url") or ""),
    )
    badge_class = (
        "is-complete" if item.get("editorial_status") == "complete" else "is-needs-review"
    )
    return f"""<!doctype html>
<html lang="es-MX">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(str(title))} · Radar Regulatorio MX</title>
  <meta name="description" content="{html.escape(str(description or ''), quote=True)}">
  <meta property="og:title" content="{html.escape(str(title), quote=True)}">
  <meta property="og:description" content="{html.escape(str(description or ''), quote=True)}">
  <meta property="og:type" content="article">
  <meta property="og:site_name" content="Radar Regulatorio MX">
  <meta property="og:url" content="{canonical}">
  <link rel="canonical" href="{canonical}">
  <link rel="stylesheet" href="../styles.css?v={STATIC_ASSET_VERSION}">
  <script type="application/ld+json">{json_ld}</script>
</head>
<body>
  <a class="skip-link" href="#contenido">Saltar al contenido</a>
  <header class="site-header">
    <nav aria-label="Principal"><a href="../index.html">Radar Regulatorio MX</a></nav>
  </header>
  <main id="contenido" class="detail-page" tabindex="-1">
    <p><a href="../index.html">Volver al radar</a></p>
    <p><span class="editorial-badge {badge_class}">{status}</span></p>
    <h1>{html.escape(str(title))}</h1>
    <dl>
      <dt>Fecha oficial</dt>
      <dd>{html.escape(str(item.get('official_published_at') or 'No indicada'))}</dd>
      <dt>Detectada</dt><dd>{html.escape(str(item.get('detected_at') or 'No indicada'))}</dd>
      <dt>Fuente</dt><dd>{html.escape(str(item.get('source') or 'No indicada'))}</dd>
    </dl>
{body}
{identifiers}
{case_html}
    <section aria-labelledby="titulo-oficial">
      <h2 id="titulo-oficial">Título oficial</h2>
      <p>{html.escape(str(item.get('official_title') or 'No indicado'))}</p>
    </section>
    <h2>Evidencia oficial</h2>
    <ul>{evidence_links or f'<li><a href="{source_url}">Abrir fuente oficial</a></li>'}</ul>
  </main>
  <footer><p>Radar Regulatorio MX · Evidencia primaria y revisión editorial explícita.</p></footer>
</body>
</html>
"""


def _article_json_ld(
    item: dict[str, Any],
    *,
    title: str,
    description: str,
    canonical_url: str,
    source_url: str,
) -> str:
    """Serializa metadata ``Article`` sin permitir cerrar el bloque ``script``."""
    article: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Article",
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical_url},
        "url": canonical_url,
        "headline": title,
        "description": description,
        "inLanguage": "es-MX",
        "publisher": {"@type": "Organization", "name": "Radar Regulatorio MX"},
    }
    optional = {
        "datePublished": item.get("official_published_at") or item.get("published_at"),
        "dateModified": item.get("last_seen_at") or item.get("detected_at"),
        "identifier": item.get("id"),
        "isBasedOn": source_url if source_url.startswith(("http://", "https://")) else None,
    }
    article.update({key: value for key, value in optional.items() if value})
    author = item.get("issuing_body") or item.get("authority")
    if author:
        article["author"] = {"@type": "Organization", "name": str(author)}
    categories = item.get("categories")
    if isinstance(categories, list) and any(
        isinstance(value, str) and value for value in categories
    ):
        article["about"] = [value for value in categories if isinstance(value, str) and value]

    encoded = json.dumps(article, ensure_ascii=False, separators=(",", ":"))
    return encoded.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def _metadata_description(value: Any, *, limit: int = 280) -> str:
    """Mantiene metadata útil sin duplicar documentos oficiales completos."""
    normalized = " ".join(str(value or "").split())
    if len(normalized) <= limit:
        return normalized
    shortened = normalized[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return (shortened or normalized[: limit - 1]).rstrip() + "…"


def _structured_card_html(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    sections: list[str] = []
    for heading, content in re.findall(
        r"^##\s+(.+?)\s*\n+(.*?)(?=^##\s+|\Z)", value, flags=re.MULTILINE | re.DOTALL
    ):
        heading = heading.strip()
        if heading == "Fuente":
            continue
        section_id = "card-" + re.sub(r"[^a-z0-9]+", "-", heading.casefold()).strip("-")
        paragraphs = "".join(
            f"<p>{html.escape(paragraph.strip())}</p>"
            for paragraph in re.split(r"\n\s*\n", content.strip())
            if paragraph.strip()
        )
        sections.append(
            f'<section aria-labelledby="{section_id}"><h2 id="{section_id}">'
            f"{html.escape(heading)}</h2>{paragraphs}</section>"
        )
    return "".join(sections)


def _definition_html(value: Any, *, section_id: str, title: str) -> str:
    if not isinstance(value, dict):
        return ""
    rows = "".join(
        f"<dt>{html.escape(_human_label(str(label)))}</dt><dd>{html.escape(str(content))}</dd>"
        for label, content in value.items()
        if isinstance(content, str) and content.strip()
    )
    if not rows:
        return ""
    return (
        f'<section aria-labelledby="{section_id}"><h2 id="{section_id}">'
        f"{html.escape(title)}</h2><dl>{rows}</dl></section>"
    )


def _human_label(value: str) -> str:
    labels = {
        "source_id": "Identificador de la fuente",
        "case_number": "Número de caso",
        "primary_url": "Fuente principal",
        "detail_url": "Ficha oficial",
    }
    return labels.get(value, value.replace("_", " ").strip().capitalize())


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
            if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(
                item.get("theme"), str
            ):
                result[item["id"]] = item["theme"]
    return result


def _bounded_reason(value: Any) -> str:
    selected = words(value if isinstance(value, str) else "")
    if not selected:
        selected = words(
            "Registro oficial pendiente de revisión editorial; consulta la fuente primaria para "
            "confirmar su alcance jurídico"
        )
    if len(selected) < WHY_MIN_WORDS:
        selected.extend(words("Consulta la fuente primaria para confirmar su alcance jurídico"))
    return " ".join(selected[:WHY_MAX_WORDS]).rstrip(" .,;:") + "."


def _plain_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
