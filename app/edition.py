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

from app.state import load_committed_state
from app.text import words

SCHEMA_VERSION = 8
MAX_SIGNALS = 7
WHY_MIN_WORDS = 12
WHY_MAX_WORDS = 32
TEASER_MAX_WORDS = 70
SUMMARY_MIN_WORDS = 300
SUMMARY_MAX_WORDS = 1000
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
    "source_id",
    "title",
    "summary_teaser",
    "summary",
    "official_title",
    "editorial_status",
    "review_reason",
    "source",
    "url",
    "canonical_url",
    "detail_url",
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
    previous_by_case = _case_identity_index(previous_items)
    current_items = [
        _reconcile_item(
            item,
            _previous_item(item, previous_items, previous_by_case),
            current["generated_at"],
        )
        for item in current["items"]
    ]
    historical_items = [
        _reconcile_item(
            item,
            _previous_item(item, previous_items, previous_by_case),
            current["generated_at"],
        )
        for item in historical_transport
        if isinstance(item, dict)
    ]
    current["items"] = current_items
    current["total_items"] = len(current_items)
    if not preserve_edition:
        current["edition"] = build_default_edition(
            current, edition_date_from_generated_at(current["generated_at"])
        )

    permanent_items = dict(previous_items)
    superseded_item_ids: set[str] = set()
    for item in (*historical_items, *current_items):
        superseded_item_ids.update(_remove_legacy_case_aliases(permanent_items, item))
        permanent_items[item["id"]] = item
    state_payloads = _state_payloads(data_dir, pending_state)
    cut_id = compute_cut_id(
        current,
        permanent_items.values(),
        state_payloads,
    )
    current["cut_id"] = cut_id

    from app.validation import validate_publications_payload

    report = validate_publications_payload(current)
    if not report.ok:
        raise PublishError("corte inválido: " + "; ".join(report.errors[:8]))

    artifacts: dict[Path, bytes] = {}
    publications_rel = publications_path.relative_to(site_root)
    edition_rel = edition_path.relative_to(site_root)
    artifacts[publications_rel] = _json_bytes(current)
    artifacts[edition_rel] = _json_bytes(build_edition_artifact(current))

    archive_months: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in permanent_items.values():
        month = str(item.get("official_published_at") or item.get("published_at") or "")[:7]
        if len(month) == 7:
            grouped.setdefault(month, []).append(item)
        key = item_key(str(item["id"]))
        artifacts[data_prefix / "items" / f"{key}.json"] = _json_bytes(
            {"schema_version": SCHEMA_VERSION, "cut_id": cut_id, "item": item}
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
                *(
                    data_prefix / "items" / f"{item_key(item_id)}.json"
                    for item_id in superseded_item_ids
                ),
                *(Path("notas") / f"{item_key(item_id)}.html" for item_id in superseded_item_ids),
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
            staged = stage_root / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(content)

        from app.validation import validate_site_artifacts

        staged_report = validate_site_artifacts(stage_root / manifest_rel, root=stage_root)
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
    manifest_path = data_dir / "manifest.json"
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
    source = str(result.get("source") or "Fuente desconocida").strip()
    source_id = str(result.get("source_id") or "").strip()
    item_id = str(result.get("id") or "").strip()
    if not source_id and ":" in item_id:
        source_id = item_id.split(":", 1)[1]
    url = str(result.get("canonical_url") or result.get("url") or "").strip()
    canonical_url = _canonical_url(url)
    source_id = source_id or canonical_url
    item_id = item_id or f"{source.casefold()}:{source_id}"
    official_date = str(
        result.get("official_published_at") or result.get("published_at") or generated_at[:10]
    )[:10]
    detected_at = _iso_datetime(result.get("detected_at") or generated_at)

    result.update(
        {
            "id": item_id,
            "source_id": source_id,
            "source": source,
            "url": str(result.get("url") or canonical_url),
            "canonical_url": canonical_url,
            "detail_url": f"notas/{item_key(item_id)}.html",
            "official_published_at": official_date,
            "published_at": official_date,
            "detected_at": detected_at,
            "first_seen_at": _iso_datetime(result.get("first_seen_at") or detected_at),
            "last_seen_at": _iso_datetime(result.get("last_seen_at") or detected_at),
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
    # Nunca confiar en un digest aportado por el payload: la huella se deriva
    # de la evidencia oficial normalizada. Así, cambiar el título oficial u
    # otro dato sustantivo sin actualizar el hash no puede conservar una
    # editorial obsoleta.
    result["content_hash"] = _valid_or_computed_hash(result)

    if result.get("editorial_status") not in {"complete", "needs_review"}:
        result["editorial_status"] = "needs_review"
    if result["editorial_status"] == "needs_review":
        for field in ("title", "summary_teaser", "summary", "card_body", "detail_markdown"):
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


def _reconcile_item(
    incoming: dict[str, Any], previous: dict[str, Any] | None, generated_at: str
) -> dict[str, Any]:
    item = deepcopy(incoming)
    if previous is None:
        return item
    previous = _normalize_item(previous, generated_at)
    item["first_seen_at"] = previous.get("first_seen_at", item["first_seen_at"])
    item["last_seen_at"] = generated_at
    if item["content_hash"] != previous.get("content_hash"):
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

    item["detected_at"] = previous.get("detected_at", item["detected_at"])
    if item.get("editorial_status") != "complete" and previous.get(
        "editorial_status"
    ) == "complete":
        for field in EDITORIAL_FIELDS:
            item[field] = deepcopy(previous.get(field))
        item["editorial_status"] = "complete"
        item["review_reason"] = None
        item["ai_generated"] = True
    return item


def _load_permanent_items(
    data_dir: Path, publications_path: Path
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    # Los índices ligeros sirven únicamente como fallback de migraciones que
    # todavía no cuenten con envelopes de detalle.
    for path in sorted(data_dir.joinpath("archive").glob("????-??.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        generated_at = _iso_datetime(payload.get("generated_at"))
        for item in payload.get("items", []):
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                result[item["id"]] = _normalize_item(item, generated_at)

    # Los envelopes de detalle son la fuente histórica primaria: contienen
    # evidencia y editorial completas aunque el ítem haya salido de la
    # ventana móvil. `publications.json` vigente tiene la última palabra.
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
        try:
            payload = json.loads(publications_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            generated_at = _iso_datetime(payload.get("generated_at"))
            for item in payload.get("items", []):
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    result[item["id"]] = _normalize_item(item, generated_at)
    return result


def _manifest_item_paths(data_dir: Path) -> tuple[Path, ...]:
    """Carga sólo envelopes que pertenecen al último corte confirmado.

    Antes de v8 no existía manifiesto; en ese caso se conserva el glob como
    fallback de migración. Una vez que hay manifiesto, ignorar archivos
    huérfanos evita resucitar ids legacy sustituidos en corridas posteriores.
    """

    manifest_path = data_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return tuple(sorted(data_dir.joinpath("items").glob("*.json")))
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if not isinstance(artifacts, dict):
        return tuple(sorted(data_dir.joinpath("items").glob("*.json")))
    site_root = data_dir.parent if data_dir.name == "data" else data_dir
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
        paths.append(site_root / relative)
    return tuple(sorted(paths))


def _previous_item(
    incoming: dict[str, Any],
    previous_items: dict[str, dict[str, Any]],
    previous_by_case: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any] | None:
    return previous_items.get(str(incoming.get("id") or "")) or previous_by_case.get(
        _case_identity(incoming)
    )


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
            states[path.stem] = payload
    legacy = {"icsid": data_dir / "icsid_snapshot.json", "tmec": data_dir / "tmec_snapshot.json"}
    for name, path in legacy.items():
        if name in states or not path.exists():
            continue
        payload = load_committed_state(path, source=name)
        if payload is not None:
            states[name] = payload
    if isinstance(pending, dict):
        states.update(pending)
    return states


def _commit_staged_artifacts(
    stage_root: Path,
    site_root: Path,
    paths: tuple[Path, ...],
    *,
    retire: tuple[Path, ...] = (),
) -> None:
    backup_root = stage_root / ".backup"
    committed: list[tuple[Path, Path | None]] = []
    try:
        for relative in paths:
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
    """Retira sólo índices/estado reemplazables que ya no pertenecen al corte.

    Las fichas y notas son permanentes y nunca se eliminan aquí. Las rutas se
    toman exclusivamente del manifiesto anterior, se validan como relativas y
    se mueven al backup de la misma transacción para permitir rollback.
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


def _valid_or_computed_hash(item: dict[str, Any]) -> str:
    official = {
        field: item.get(field)
        for field in (
            "source",
            "source_id",
            "canonical_url",
            "official_title",
            "description",
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
  <link rel="stylesheet" href="../styles.css">
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
