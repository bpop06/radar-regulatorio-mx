from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.calendars import validate_calendars_payload
from app.config import Settings
from app.edition import prepare_payload, write_site_artifacts
from app.editorial import EditorialError, apply_editorial
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
    retract_parser.add_argument("--edition-output", type=Path, default=None)

    restore_parser = subparsers.add_parser(
        "restore-items",
        help="restaura fichas desde el registro transaccional de retiros",
    )
    restore_parser.add_argument("ids", nargs="+")
    restore_parser.add_argument(
        "--input", type=Path, default=Path("docs/data/publications.json")
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
            write_site_artifacts(payload, args.output, args.edition_output)
            print(f"Publicadas {payload['total_items']} novedades en {args.output}")
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
        payload = asyncio.run(collect(settings, args.days))
        report = validate_publications_payload(payload)
        _print_source_status(payload)
        _print_validation_report(report)
        if not report.ok:
            raise SystemExit(1)

        # La publicación del día nunca debe depender de la base histórica:
        # primero se escribe el JSON público y después se persiste la corrida
        # como mejor esfuerzo (una DB corrupta o un disco lleno solo advierten).
        write_site_artifacts(payload, args.output, args.edition_output)
        print(f"Publicadas {payload['total_items']} novedades en {args.output}")
        try:
            published_payload = json.loads(args.output.read_text(encoding="utf-8"))
            with Storage(args.db or settings.database_path) as storage:
                run_id = storage.save_run(published_payload)
        except Exception as exc:
            print(
                f"Advertencia: no se pudo guardar la corrida en la base local: {exc}",
                file=sys.stderr,
            )
        else:
            print(f"Corrida {run_id} guardada en la base local")
    elif args.command == "export-site":
        with Storage(args.db or settings.database_path) as storage:
            payload = storage.export_payload()
        payload = prepare_payload(payload, settings.local_timezone, force=True)
        report = validate_publications_payload(payload)
        _print_validation_report(report)
        if not report.ok:
            raise SystemExit(1)
        write_site_artifacts(payload, args.output, args.edition_output)
        print(f"Exportadas {payload['total_items']} novedades en {args.output}")
    elif args.command == "storage-report":
        with Storage(args.db or settings.database_path) as storage:
            info = storage.report()
        size_kb = info.size_bytes / 1024
        print(f"Base local: {info.database_path}")
        print(f"Tamaño: {size_kb:.1f} KB")
        print(f"Corridas: {info.runs}")
        print(f"Publicaciones únicas: {info.documents}")
        print(f"Última corrida: {info.last_generated_at or 'sin corridas'}")
    elif args.command == "apply-editorial":
        try:
            applied = apply_editorial(
                args.edits,
                args.input,
                Path(args.db) if args.db else Path(settings.database_path),
                args.edition_output,
            )
        except EditorialError as exc:
            print(f"Error editorial: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        report = validate_publications_payload(payload)
        _print_validation_report(report)
        if not report.ok:
            raise SystemExit(1)
        print(f"Editorial aplicada a {applied} publicaciones en {args.input}")
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
            write_site_artifacts(payload, args.input, args.edition_output)
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
            )
        except RetractionError as exc:
            print(f"Error de restauración: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"Restauradas {count} fichas en {args.input}")
    elif args.command == "build-edition":
        payload = prepare_payload(
            json.loads(args.input.read_text(encoding="utf-8")),
            settings.local_timezone,
            force=True,
        )
        report = validate_publications_payload(payload)
        _print_validation_report(report)
        if not report.ok:
            raise SystemExit(1)
        write_site_artifacts(payload, args.input, args.output)
        print(
            f"Edición {payload['edition']['edition_date']} generada en "
            f"{args.output or args.input.with_name('edition.json')}"
        )
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
        report = validate_publications_payload(payload)
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
            artifact_report = validate_site_artifacts(manifest_path)
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


def _print_validation_report(report: ValidationReport) -> None:
    for warning in report.warnings:
        print(f"Advertencia: {warning}", file=sys.stderr)
    for error in report.errors:
        print(f"Error: {error}", file=sys.stderr)


if __name__ == "__main__":
    main()
