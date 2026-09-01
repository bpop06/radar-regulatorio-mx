from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.calendars import validate_calendars_payload
from app.config import Settings
from app.edition import (
    PublishError,
    load_manifested_items,
    prepare_payload,
    write_site_artifacts,
)
from app.editorial import EditorialError, apply_editorial
from app.extraction import EXTRACTOR_VERSION, DocumentExtractor, ExtractionRequest
from app.international_history import (
    InternationalHistoryError,
    rebuild_manifested_international_history,
)
from app.pipeline import collect
from app.retractions import RetractionError, restore_items, retract_items
from app.sources.base import SourceContractError
from app.sources.certification import assert_relaunch_ready, certification_report
from app.storage import Storage
from app.validation import (
    ValidationReport,
    validate_freshness,
    validate_publications_payload,
    validate_site_artifacts,
)

COLLECTION_REUSE_FINGERPRINT_VERSION = 1


def _collection_fingerprint(settings: Settings, days: int | None) -> dict[str, Any]:
    """Describe los parámetros que pueden cambiar una corrida reutilizable."""

    return {
        "version": COLLECTION_REUSE_FINGERPRINT_VERSION,
        "lookback_days": days if days is not None else settings.lookback_days,
        "minimum_relevance": settings.minimum_relevance,
        "local_timezone": settings.local_timezone,
        "request_timeout": settings.request_timeout,
        "source_retries": settings.source_retries,
        "source_retry_backoff_seconds": settings.source_retry_backoff_seconds,
        "source_deadline_seconds": settings.source_deadline_seconds,
        "collection_deadline_seconds": settings.collection_deadline_seconds,
        "extractor_version": EXTRACTOR_VERSION,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recolecta novedades regulatorias mexicanas")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect", help="recolecta y escribe el JSON público")
    collect_parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/data/publications.json"),
    )
    collect_parser.add_argument("--days", type=int)
    collect_parser.add_argument("--dry-run", action="store_true")
    collect_parser.add_argument(
        "--rebuild-stateful-history",
        action="store_true",
        help=(
            "reconstruye las fichas permanentes CIADI/T-MEC aunque exista snapshot, "
            "sin publicarlas como novedades del día"
        ),
    )
    collect_parser.add_argument("--edition-output", type=Path, default=None)

    certify_parser = subparsers.add_parser(
        "certify-sources",
        help="verifica que las 18 fuentes estén listas para el relanzamiento inicial",
    )
    certify_parser.add_argument("--days", type=int)

    research_parser = subparsers.add_parser(
        "research",
        help="recolecta, persiste la corrida en la base local y escribe el JSON público",
    )
    research_parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/data/publications.json"),
    )
    research_parser.add_argument("--days", type=int)
    research_parser.add_argument("--db", type=Path, default=None)
    research_parser.add_argument("--edition-output", type=Path, default=None)

    export_parser = subparsers.add_parser(
        "export-site",
        help="regenera el JSON público desde la última corrida guardada en la base local",
    )
    export_parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/data/publications.json"),
    )
    export_parser.add_argument("--db", type=Path, default=None)
    export_parser.add_argument("--edition-output", type=Path, default=None)
    export_parser.add_argument(
        "--complete-only",
        action="store_true",
        default=True,
        help="publica exclusivamente extracción y editorial completas (obligatorio en v8)",
    )
    export_parser.add_argument("--details-dir", type=Path, default=None)

    prepare_editorial_parser = subparsers.add_parser(
        "prepare-editorial",
        help="recolecta, extrae evidencia completa y actualiza la cola privada",
    )
    prepare_editorial_parser.add_argument("--days", type=int)
    prepare_editorial_parser.add_argument("--db", type=Path, default=None)
    prepare_editorial_parser.add_argument(
        "--bootstrap-input",
        type=Path,
        default=None,
        help="importa una sola vez el archivo público actual a la cola privada",
    )

    queue_parser = subparsers.add_parser(
        "editorial-queue", help="exporta la cola privada pendiente para Codex"
    )
    queue_parser.add_argument("--db", type=Path, default=None)
    queue_parser.add_argument("--output", type=Path, required=True)

    report_parser = subparsers.add_parser(
        "storage-report",
        help="tamaño y contenido de la base local",
    )
    report_parser.add_argument("--db", type=Path, default=None)

    editorial_parser = subparsers.add_parser(
        "apply-editorial",
        help="aplica títulos/resúmenes/cuerpos editoriales validados a ítems existentes",
    )
    editorial_parser.add_argument(
        "edits",
        type=Path,
        help="JSON con {items:[{id,title,summary,card_body}]}",
    )
    editorial_parser.add_argument(
        "--input",
        type=Path,
        default=Path("docs/data/publications.json"),
    )
    editorial_parser.add_argument("--db", type=Path, default=None)
    editorial_parser.add_argument("--edition-output", type=Path, default=None)
    editorial_parser.add_argument(
        "--legacy-migration",
        action="store_true",
        help="habilita explícitamente la migración v7 sin base privada",
    )

    international_history_parser = subparsers.add_parser(
        "rebuild-international-history",
        help="enriquece una sola vez fichas históricas CIJ/CPI desde evidencia ya almacenada",
    )
    international_history_parser.add_argument(
        "--input",
        type=Path,
        default=Path("docs/data/publications.json"),
    )
    international_history_parser.add_argument("--edition-output", type=Path, default=None)
    international_history_parser.add_argument("--dry-run", action="store_true")

    retract_parser = subparsers.add_parser(
        "retract-items",
        help="retira fichas falsas de forma transaccional y conserva rollback",
    )
    retract_parser.add_argument("ids", nargs="+")
    retract_parser.add_argument("--reason", required=True)
    retract_parser.add_argument(
        "--input", type=Path, default=Path("docs/data/publications.json")
    )
    retract_parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="base privada cuyo directorio conserva la bitácora de restauración",
    )
    retract_parser.add_argument("--edition-output", type=Path, default=None)

    restore_parser = subparsers.add_parser(
        "restore-items",
        help="restaura fichas desde el registro transaccional de retiros",
    )
    restore_parser.add_argument("ids", nargs="+")
    restore_parser.add_argument(
        "--input", type=Path, default=Path("docs/data/publications.json")
    )
    restore_parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="base privada cuyo directorio conserva la bitácora de restauración",
    )
    restore_parser.add_argument("--edition-output", type=Path, default=None)

    edition_parser = subparsers.add_parser(
        "build-edition",
        help="actualiza el contrato v8 y genera el corte transaccional del sitio",
    )
    edition_parser.add_argument(
        "--input",
        type=Path,
        default=Path("docs/data/publications.json"),
    )
    edition_parser.add_argument("--output", type=Path, default=None)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument(
        "--input",
        type=Path,
        default=Path("docs/data/publications.json"),
    )
    validate_parser.add_argument(
        "--require-v8",
        action="store_true",
        help="exige schema v8 y manifest hermano; usar en CI de relanzamiento/producción",
    )
    validate_parser.add_argument(
        "--max-age-hours",
        type=float,
        default=None,
        help="falla si generated_at es más antiguo; por defecto no aplica gate histórico",
    )

    calendars_parser = subparsers.add_parser(
        "validate-calendars",
        help="valida el contrato de los calendarios de días hábiles",
    )
    calendars_parser.add_argument(
        "--input",
        type=Path,
        default=Path("docs/data/calendars.json"),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings()
    if args.command == "collect":
        payload = asyncio.run(
            collect(
                settings,
                args.days,
                dry_run=args.dry_run,
                rebuild_stateful_history=args.rebuild_stateful_history,
            )
        )
        report = validate_publications_payload(payload)
        _print_source_status(payload)
        _print_validation_report(report)
        if not report.ok:
            raise SystemExit(1)

        if args.dry_run:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                "Error: collect no publica cortes v8; usa prepare-editorial, "
                "apply-editorial y export-site --complete-only.",
                file=sys.stderr,
            )
            raise SystemExit(2)
    elif args.command == "certify-sources":
        payload = asyncio.run(collect(settings, args.days, dry_run=True))
        sources = payload.get("sources")
        report = certification_report(sources if isinstance(sources, list) else [])
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        try:
            assert_relaunch_ready(report)
        except SourceContractError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
    elif args.command == "research":
        print(
            "Error: research fue sustituido por el flujo privado "
            "prepare-editorial → apply-editorial → export-site --complete-only.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    elif args.command == "export-site":
        with Storage(
            args.db or settings.database_path, max_bytes=settings.state_max_bytes
        ) as storage:
            payload = storage.export_payload()
            all_private_items = storage.all_documents()
        if args.complete_only:
            complete_private_items = [
                item
                for item in all_private_items
                if item.get("extraction_status") == "complete"
                and item.get("editorial_status") == "complete"
            ]
            current_ids = {
                str(item.get("id")) for item in payload.get("items", [])
            }
            # El writer recibe también los actuales incompletos: no llegan al
            # índice público, pero sí invalidan y retiran una ficha previa.
            payload["items"] = [
                item for item in all_private_items if str(item.get("id")) in current_ids
            ]
            payload["_historical_items"] = [
                item for item in complete_private_items if str(item.get("id")) not in current_ids
            ]
            payload["total_items"] = len(payload["items"])
            mismatches = [
                str(item.get("id"))
                for item in complete_private_items
                if not isinstance(item.get("evidence"), dict)
                or item["evidence"].get("content_hash")
                != item.get("source_content_hash")
                or item["evidence"].get("extraction_method")
                != item.get("extraction_method")
                or item["evidence"].get("official_url")
                not in {item.get("url"), item.get("canonical_url")}
                or item["evidence"].get("retrieved_at")
                != item.get("extraction_retrieved_at")
                or _private_coverage_refs(item) != _private_inventory_refs(item)
            ]
            if mismatches:
                print(
                    "Error: evidencia editorial desalineada con extracción privada: "
                    + ", ".join(mismatches[:5]),
                    file=sys.stderr,
                )
                raise SystemExit(1)
        payload = prepare_payload(payload, settings.local_timezone, force=True)
        # La mezcla privada puede incluir pendientes para invalidar/retirar
        # sus versiones anteriores. El writer construye y valida después la
        # proyección pública complete-only.
        if not args.complete_only:
            report = validate_publications_payload(payload, public_only=False)
            _print_validation_report(report)
            if not report.ok:
                raise SystemExit(1)
        write_site_artifacts(
            payload,
            args.output,
            args.edition_output,
            complete_only=args.complete_only,
            details_dir=args.details_dir,
        )
        print(f"Exportadas {payload['total_items']} novedades en {args.output}")
    elif args.command == "prepare-editorial":
        started = time.monotonic()
        database = args.db or Path(settings.database_path)
        reused_run = False
        collection_clock: str | None = None
        collection_fingerprint = _collection_fingerprint(settings, args.days)
        private_payload: dict[str, Any] | None = None
        if args.bootstrap_input is not None:
            raw_bootstrap = json.loads(
                args.bootstrap_input.read_text(encoding="utf-8")
            )
            manifest_path = args.bootstrap_input.with_name("manifest.json")
            if manifest_path.exists():
                try:
                    manifested = load_manifested_items(args.bootstrap_input)
                except PublishError as exc:
                    raise SystemExit(f"Error: manifiesto bootstrap inválido: {exc}") from exc
                current_ids = {
                    str(item.get("id")) for item in raw_bootstrap.get("items", [])
                }
                raw_bootstrap["items"] = [
                    *raw_bootstrap.get("items", []),
                    *(
                        item
                        for item_id, item in manifested.items()
                        if item_id not in current_ids
                    ),
                ]
                raw_bootstrap["total_items"] = len(raw_bootstrap["items"])
            private_payload = prepare_payload(
                raw_bootstrap, settings.local_timezone, force=True
            )
            for item in private_payload.get("items", []):
                item.update(
                    extraction_status="pending",
                    source_revalidation_status="pending",
                    editorial_status="needs_review",
                    ai_generated=False,
                )
                for field in (
                    "title", "summary_teaser", "summary", "card_body",
                    "detail_markdown", "executive_summary", "detailed_summary",
                    "impacts", "recommended_actions", "evidence", "coverage",
                ):
                    item[field] = None
            private_payload["total_items"] = len(private_payload.get("items", []))
            collection_clock = datetime.now(UTC).isoformat()
        elif Path(database).exists():
            with Storage(database, max_bytes=settings.state_max_bytes) as existing:
                metrics = existing.latest_cut_metrics()
                generated = (
                    metrics.get("last_network_collection_at")
                    if metrics else None
                )
                collection_clock = str(generated) if generated else None
                try:
                    age = (
                        datetime.now(UTC)
                        - datetime.fromisoformat(str(generated).replace("Z", "+00:00"))
                    ).total_seconds()
                except (TypeError, ValueError):
                    age = float("inf")
                if (
                    0 <= age <= 600
                    and metrics is not None
                    and metrics.get("collection_fingerprint") == collection_fingerprint
                ):
                    private_payload = existing.export_payload()
                    reused_run = not any(
                        item.get("source_revalidation_status") == "pending"
                        for item in private_payload.get("items", [])
                    )
                    if not reused_run:
                        private_payload = None
        if private_payload is None:
            payload = asyncio.run(collect(settings, args.days))
            private_payload = prepare_payload(
                payload, settings.local_timezone, force=True
            )
            collection_clock = datetime.now(UTC).isoformat()
        with Storage(database, max_bytes=settings.state_max_bytes) as storage:
            if not reused_run:
                storage.save_run(private_payload)
            extractor = DocumentExtractor(
                cache=storage,
                timeout=settings.request_timeout,
                user_agent=settings.user_agent,
            )
            queued_payload = storage.export_payload()
            queued_by_id = {
                str(item.get("id")): item for item in queued_payload.get("items", [])
            }
            requests: list[ExtractionRequest] = []
            for item in ([] if reused_run else queued_payload.get("items", [])):
                request = ExtractionRequest(
                    str(item["id"]), str(item["url"]),
                    str(item.get("content_hash") or ""),
                )
                if item.get("extraction_status") == "complete":
                    cached = extractor.cached_source(request)
                    if (
                        item.get("extraction_version") == EXTRACTOR_VERSION
                        and cached is not None
                        and cached.content_hash == item.get("source_content_hash")
                        and extractor.source_is_unchanged(request, cached)
                    ):
                        if cached is not None:
                            # Copia el cache al nuevo hash de metadatos sin
                            # descargar ni reextraer el documento.
                            extractor._store(request, cached)  # noqa: SLF001
                        storage.update_document_fields_atomic(
                            {
                                str(item["id"]): {
                                    "source_revalidation_status": "complete"
                                }
                            }
                        )
                        continue
                    request = ExtractionRequest(
                        request.document_id,
                        request.url,
                        request.evidence_hash,
                        force_refresh=True,
                    )
                else:
                    # Una observación nueva/pending no puede adoptar una
                    # extracción vieja sólo porque coincida el metahash.
                    request = ExtractionRequest(
                        request.document_id,
                        request.url,
                        request.evidence_hash,
                        force_refresh=True,
                    )
                requests.append(request)
            results = extractor.extract_many(requests)
            updates: dict[str, dict[str, Any]] = {}
            for result in results:
                fields: dict[str, Any] = {
                    "extraction_status": result.status,
                    "source_revalidation_status": "complete",
                    "source_content_hash": result.content_hash,
                    "extraction_method": result.extraction_method,
                    "extraction_retrieved_at": result.retrieved_at,
                    "extraction_diagnostics": result.diagnostics,
                    "extraction_version": EXTRACTOR_VERSION,
                }
                previous_item = queued_by_id.get(result.document_id, {})
                new_inventory = (
                    _source_section_inventory(result.markdown, result.diagnostics)
                    if result.status == "complete"
                    else None
                )
                source_changed = (
                    previous_item.get("source_content_hash")
                    and previous_item.get("source_content_hash") != result.content_hash
                )
                source_unchanged = (
                    result.status == "complete"
                    and previous_item.get("source_content_hash") == result.content_hash
                    and previous_item.get("extraction_version") == EXTRACTOR_VERSION
                    and previous_item.get("extraction_method") == result.extraction_method
                    and previous_item.get("source_sections") == new_inventory
                )
                if source_changed or not source_unchanged:
                    reason = (
                        "Los bytes de la fuente oficial cambiaron; requiere una nueva "
                        "auditoría editorial."
                        if source_changed
                        else "La extracción o su cobertura cambió; requiere una nueva "
                        "auditoría editorial."
                    )
                    fields.update(
                        {
                            "extracted_text": None,
                            "source_sections": None,
                            "editorial_status": "needs_review",
                            "review_reason": reason,
                            "ai_generated": False,
                            "executive_summary": None,
                            "detailed_summary": None,
                            "impacts": None,
                            "recommended_actions": None,
                            "evidence": None,
                            "coverage": None,
                            "title": None,
                            "summary_teaser": None,
                            "summary": None,
                            "card_body": None,
                            "detail_markdown": None,
                            "case_facts": "",
                            "case_claim": "",
                            "case_outcome": "",
                            "case_reasoning": "",
                            "case_amount": "",
                        }
                    )
                if source_unchanged:
                    # La revalidación descargó el cuerpo porque no había ETag
                    # fuerte, pero el SHA-256 real confirma que nada cambió.
                    fields["extraction_retrieved_at"] = previous_item.get(
                        "extraction_retrieved_at", result.retrieved_at
                    )
                    fields["editorial_status"] = previous_item.get(
                        "editorial_status", "needs_review"
                    )
                    fields["review_reason"] = previous_item.get("review_reason")
                    fields["source_sections"] = new_inventory
                elif result.status == "complete":
                    fields["extracted_text"] = result.markdown
                    fields["source_sections"] = new_inventory
                    fields["editorial_status"] = "needs_review"
                    fields["review_reason"] = (
                        "Evidencia completa; requiere interpretación editorial."
                    )
                else:
                    fields["editorial_status"] = "needs_review"
                    fields["extracted_text"] = None
                    fields["source_sections"] = None
                    fields["review_reason"] = result.error or (
                        "La fuente no pudo extraerse íntegramente."
                    )
                updates[result.document_id] = fields
            storage.update_document_fields_atomic(updates)
            post_payload = storage.export_payload()
            post_items = post_payload.get("items", [])
            durations = [result.duration_seconds for result in results]
            reused_complete = sum(
                item.get("extraction_status") == "complete"
                and item.get("source_revalidation_status") in {None, "complete"}
                and str(item.get("id")) not in updates
                for item in post_items
            )
            cached_results = sum(result.cache_hit for result in results)
            storage_info = storage.report()
            metrics = {
                "generated_at": datetime.now(UTC).isoformat(),
                "last_network_collection_at": collection_clock,
                "collection_fingerprint": collection_fingerprint,
                "run_reused": reused_run,
                "duration_seconds": time.monotonic() - started,
                "p50_seconds": statistics.median(durations) if durations else 0.0,
                "p95_seconds": _percentile(durations, 0.95),
                "cache_hits": reused_complete + cached_results,
                "cache_rate": (
                    (reused_complete + cached_results) / max(1, len(post_items))
                ),
                "ocr_documents": sum("ocr" in result.extraction_method for result in results),
                "failures": sum(
                    item.get("extraction_status") != "complete" for item in post_items
                ),
                "tokens": 0,
                "retained_items": sum(
                    item.get("editorial_status") == "complete"
                    for item in post_items
                ),
                "total_items": len(post_items),
                "state_size_bytes": storage_info.size_bytes + storage_info.sidecar_bytes,
                "state_max_bytes": storage_info.state_max_bytes,
                "cache_entries": storage_info.cache_entries,
                "cache_bytes": storage_info.cache_bytes,
            }
            storage.save_cut_metrics(metrics)
        completed = sum(result.status == "complete" for result in results)
        print(f"Preparados {len(results)} registros; {completed} con extracción completa")
    elif args.command == "editorial-queue":
        requested_output = args.output.expanduser()
        output_parent = requested_output.parent.resolve()
        output_path = output_parent / requested_output.name
        if requested_output.is_symlink():
            print("Error: la salida de la cola no puede ser un enlace simbólico.", file=sys.stderr)
            raise SystemExit(2)
        repository_root = Path(__file__).resolve().parents[1]
        if output_path == repository_root or repository_root in output_path.parents:
            print(
                "Error: la cola contiene fuentes íntegras y debe escribirse fuera del repositorio.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        with Storage(
            args.db or settings.database_path, max_bytes=settings.state_max_bytes
        ) as storage:
            items = storage.pending_editorial_items()
        grouped: dict[str, dict[str, Any]] = {}
        for item in items:
            evidence_hash = str(
                item.get("source_content_hash") or item.get("content_hash") or item["id"]
            )
            group_key = evidence_hash
            if group_key not in grouped:
                grouped[group_key] = {
                    **item,
                    "document_ids": [item["id"]],
                    **_queue_source_payload(item),
                }
            else:
                grouped[group_key]["document_ids"].append(item["id"])
        queue_items = list(grouped.values())
        queue = {
            "schema_version": 8,
            "total_items": len(items),
            "total_tasks": len(queue_items),
            "items": queue_items,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload_bytes = (
            json.dumps(queue, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        descriptor = os.open(
            output_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(payload_bytes)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        print(
            f"Cola editorial: {len(items)} registros en {len(queue_items)} tareas "
            f"en {output_path}"
        )
    elif args.command == "storage-report":
        with Storage(
            args.db or settings.database_path, max_bytes=settings.state_max_bytes
        ) as storage:
            info = storage.report()
        size_kb = info.size_bytes / 1024
        sidecar_kb = info.sidecar_bytes / 1024
        maximum_kb = info.state_max_bytes / 1024
        print(f"Base local: {info.database_path}")
        print(f"Tamaño: {size_kb:.1f} KB (+{sidecar_kb:.1f} KB de sidecars)")
        print(f"Máximo fail-closed: {maximum_kb:.1f} KB")
        print(f"Corridas: {info.runs}")
        print(f"Publicaciones únicas: {info.documents}")
        print(f"Caché: {info.cache_entries} entradas, {info.cache_bytes / 1024:.1f} KB")
        print(f"Última corrida: {info.last_generated_at or 'sin corridas'}")
        with Storage(
            args.db or settings.database_path, max_bytes=settings.state_max_bytes
        ) as storage:
            metrics = storage.latest_cut_metrics()
        if metrics:
            print("Métricas del último corte: " + json.dumps(metrics, ensure_ascii=False))
    elif args.command == "apply-editorial":
        if args.legacy_migration and args.db:
            print("Error editorial: --legacy-migration y --db son excluyentes", file=sys.stderr)
            raise SystemExit(2)
        database = None if args.legacy_migration else (
            Path(args.db) if args.db else Path(settings.database_path)
        )
        if database is None:
            repository_root = Path(__file__).resolve().parents[1]
            migration_input = args.input.resolve()
            if migration_input == repository_root or repository_root in migration_input.parents:
                print(
                    "Error editorial: la migración legacy no puede escribir el sitio público",
                    file=sys.stderr,
                )
                raise SystemExit(2)
        edits_payload = json.loads(args.edits.read_text(encoding="utf-8"))
        token_usage = edits_payload.get("token_usage", 0)
        if (
            not isinstance(token_usage, int)
            or isinstance(token_usage, bool)
            or token_usage < 0
        ):
            print("Error editorial: token_usage debe ser un entero no negativo", file=sys.stderr)
            raise SystemExit(1)
        try:
            applied = apply_editorial(
                args.edits,
                args.input,
                database,
                args.edition_output,
            )
        except EditorialError as exc:
            print(f"Error editorial: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        if database is not None:
            with Storage(database, max_bytes=settings.state_max_bytes) as storage:
                payload = prepare_payload(
                    storage.export_payload(), settings.local_timezone, force=True
                )
                storage.add_editorial_tokens(token_usage)
        else:
            payload = prepare_payload(
                json.loads(args.input.read_text(encoding="utf-8")),
                settings.local_timezone,
                force=True,
            )
        report = validate_publications_payload(payload)
        _print_validation_report(report)
        if not report.ok:
            raise SystemExit(1)
        print(f"Editorial aplicada a {applied} publicaciones en la cola privada")
    elif args.command == "rebuild-international-history":
        try:
            historical = rebuild_manifested_international_history(args.input)
        except InternationalHistoryError as exc:
            print(f"Error de migración internacional: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        preview = {
            "dry_run": args.dry_run,
            "total_items": len(historical),
            "items": [
                {
                    "id": item.get("id"),
                    "content_hash": item.get("content_hash"),
                    "case_number": item.get("case_number"),
                    "case_parties": item.get("case_parties"),
                    "case_claim": item.get("case_claim"),
                    "case_status": item.get("case_status"),
                    "case_outcome": item.get("case_outcome"),
                    "case_amount": item.get("case_amount"),
                }
                for item in historical
            ],
        }
        if args.dry_run or not historical:
            print(json.dumps(preview, ensure_ascii=False, indent=2))
        else:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            payload["_historical_items"] = historical
            # La migración no reordena ni reelige las señales vigentes; sólo
            # cambia fichas permanentes y el cut_id que las autentica.
            payload["_preserve_edition"] = True
            write_site_artifacts(
                payload, args.input, args.edition_output, complete_only=True
            )
            print(
                f"Migradas {len(historical)} fichas históricas CIJ/CPI en {args.input}"
            )
    elif args.command == "retract-items":
        try:
            count = retract_items(
                args.input,
                args.ids,
                reason=args.reason,
                edition_path=args.edition_output,
                ledger_path=(args.db or Path(settings.database_path)).with_name(
                    "retractions.json"
                ),
            )
        except RetractionError as exc:
            print(f"Error de retiro: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"Retiradas {count} fichas en {args.input}")
    elif args.command == "restore-items":
        try:
            count = restore_items(
                args.input,
                args.ids,
                edition_path=args.edition_output,
                ledger_path=(args.db or Path(settings.database_path)).with_name(
                    "retractions.json"
                ),
            )
        except RetractionError as exc:
            print(f"Error de restauración: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"Restauradas {count} fichas en {args.input}")
    elif args.command == "build-edition":
        print(
            "Error: build-edition no puede publicar schema v8 sin atestación "
            "privada; usa export-site --db ... --complete-only.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    elif args.command == "validate":
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        manifest_path = args.input.with_name("manifest.json")
        if args.require_v8:
            if payload.get("schema_version") != 8:
                print("Error: el gate exige schema_version 8", file=sys.stderr)
                raise SystemExit(1)
            if not manifest_path.exists():
                print("Error: el gate exige manifest.json", file=sys.stderr)
                raise SystemExit(1)
        report = validate_publications_payload(
            payload,
            public_only=args.require_v8,
            index_only=args.require_v8,
        )
        _print_validation_report(report)
        if not report.ok:
            raise SystemExit(1)
        if args.max_age_hours is not None:
            freshness_errors = validate_freshness(
                payload,
                now=datetime.now(UTC),
                max_age_hours=args.max_age_hours,
            )
            for error in freshness_errors:
                print(f"Error: {error}", file=sys.stderr)
            if freshness_errors:
                raise SystemExit(1)
        if manifest_path.exists():
            artifact_report = validate_site_artifacts(
                manifest_path,
                require_complete_only=args.require_v8,
            )
            _print_validation_report(artifact_report)
            if not artifact_report.ok:
                raise SystemExit(1)
        print(f"Validación correcta: {payload['total_items']} novedades en {args.input}")
    elif args.command == "validate-calendars":
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        errors = validate_calendars_payload(payload)
        for error in errors:
            print(f"Error: {error}", file=sys.stderr)
        if errors:
            raise SystemExit(1)
        total_days = sum(len(days) for days in payload.get("days_by_organ", {}).values())
        print(
            f"Calendarios válidos: {len(payload.get('organs', []))} órganos, "
            f"{total_days} días registrados en {args.input}"
        )


def _print_source_status(payload: dict[str, Any]) -> None:
    sources = payload.get("sources")
    if not isinstance(sources, list):
        return

    for source in sources:
        if not isinstance(source, dict):
            continue
        name = source.get("source", "Fuente desconocida")
        status = source.get("status", "desconocido")
        attempts = source.get("attempts")
        attempts_text = (
            f", {attempts} intentos" if isinstance(attempts, int) and attempts > 1 else ""
        )
        if status == "ok":
            print(
                f"Fuente {name}: {source.get('items_found', 0)} registros revisados{attempts_text}",
                file=sys.stderr,
            )
        elif status == "degraded":
            warnings = source.get("warnings")
            detail = source.get("error")
            if not detail and isinstance(warnings, list) and warnings:
                detail = "; ".join(str(warning) for warning in warnings[:3])
            print(
                f"Fuente {name}: cobertura degradada{attempts_text}: "
                f"{detail or 'sin detalle'}",
                file=sys.stderr,
            )
        else:
            print(
                f"Fuente {name}: error{attempts_text}: {source.get('error', 'sin detalle')}",
                file=sys.stderr,
            )


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = round((len(ordered) - 1) * fraction)
    return ordered[min(len(ordered) - 1, max(0, int(position)))]


def _source_section_inventory(
    markdown: str, diagnostics: dict[str, Any]
) -> list[dict[str, Any]]:
    """Partición determinista y contigua que la cobertura debe agotar.

    Ningún fragmento supera 12 000 caracteres, aun cuando el documento tenga
    encabezados. Los offsets permiten demostrar ausencia de huecos y entregar
    cada parte por separado a los agentes.
    """
    chunk_size = 12_000
    heading_matches = list(re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*$", markdown))
    regions: list[tuple[int, int, str]] = []
    if heading_matches:
        if heading_matches[0].start() > 0:
            regions.append((0, heading_matches[0].start(), "Preámbulo"))
        for index, match in enumerate(heading_matches):
            end = (
                heading_matches[index + 1].start()
                if index + 1 < len(heading_matches)
                else len(markdown)
            )
            regions.append((match.start(), end, match.group(1).strip()))
    else:
        regions.append((0, len(markdown), "Documento"))

    chunks: list[dict[str, Any]] = []
    for region_start, region_end, label in regions:
        for start in range(region_start, region_end or 1, chunk_size):
            end = min(region_end, start + chunk_size)
            if start == end and markdown:
                continue
            order = len(chunks) + 1
            chunks.append(
                {
                    "id": f"chunk-{order}",
                    "label": label if end - start == region_end - region_start else (
                        f"{label} · parte {((start - region_start) // chunk_size) + 1}"
                    ),
                    "order": order,
                    "start": start,
                    "end": end,
                }
            )
    return chunks or [
        {
            "id": "chunk-1",
            "label": "Documento breve",
            "order": 1,
            "start": 0,
            "end": 0,
        }
    ]


def _private_inventory_refs(item: dict[str, Any]) -> set[str]:
    inventory = item.get("source_sections")
    if not isinstance(inventory, list):
        return set()
    return {
        str(row["id"])
        for row in inventory
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }


def _private_coverage_refs(item: dict[str, Any]) -> set[str]:
    evidence = item.get("evidence")
    coverage = item.get("coverage")
    if not isinstance(evidence, dict) or not isinstance(coverage, list):
        return set()
    locators = evidence.get("locators")
    if not isinstance(locators, list):
        return set()
    locator_refs = {
        str(row["id"])
        for row in locators
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    coverage_refs = {
        str(row["source_ref"])
        for row in coverage
        if isinstance(row, dict) and isinstance(row.get("source_ref"), str)
    }
    return locator_refs if locator_refs == coverage_refs else set()


def _queue_source_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Entrega fuentes largas como fragmentos, nunca además como un blob."""
    text = str(item.get("extracted_text") or "")
    inventory = item.get("source_sections")
    if not isinstance(inventory, list) or not inventory:
        inventory = _source_section_inventory(
            text, item.get("extraction_diagnostics") or {}
        )
    fragments: list[dict[str, Any]] = []
    for section in inventory:
        if not isinstance(section, dict):
            continue
        start = section.get("start")
        end = section.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            # Inventarios privados antiguos (página/epígrafe) se conservan
            # como localizadores; el texto breve completo sigue disponible.
            continue
        fragments.append(
            {
                "id": section.get("id"),
                "label": section.get("label"),
                "order": section.get("order"),
                "start": start,
                "end": end,
                "text": text[start:end],
            }
        )
    if len(text) > 12_000 and not fragments:
        offset_inventory = _source_section_inventory(
            text, item.get("extraction_diagnostics") or {}
        )
        fragments = [
            {
                "id": section["id"],
                "label": section["label"],
                "order": section["order"],
                "start": section["start"],
                "end": section["end"],
                "text": text[section["start"] : section["end"]],
                "source_locators": [
                    row.get("id")
                    for row in inventory
                    if isinstance(row, dict) and isinstance(row.get("id"), str)
                ],
            }
            for section in offset_inventory
        ]
    if len(text) > 12_000 or len(fragments) > 1:
        return {"extracted_text": None, "source_fragments": fragments}
    return {"extracted_text": text, "source_fragments": fragments}


def _print_validation_report(report: ValidationReport) -> None:
    for warning in report.warnings:
        print(f"Advertencia: {warning}", file=sys.stderr)
    for error in report.errors:
        print(f"Error: {error}", file=sys.stderr)


if __name__ == "__main__":
    main()
