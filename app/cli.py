from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.config import Settings
from app.pipeline import collect, write_output
from app.storage import Storage
from app.validation import ValidationReport, validate_publications_payload


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

    report_parser = subparsers.add_parser(
        "storage-report",
        help="tamaño y contenido de la base local",
    )
    report_parser.add_argument("--db", type=Path, default=None)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument(
        "--input",
        type=Path,
        default=Path("docs/data/publications.json"),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings()
    if args.command == "collect":
        payload = asyncio.run(collect(settings, args.days))
        report = validate_publications_payload(payload)
        _print_source_status(payload)
        _print_validation_report(report)
        if not report.ok:
            raise SystemExit(1)

        if args.dry_run:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            write_output(payload, args.output)
            print(f"Publicadas {payload['total_items']} novedades en {args.output}")
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
        write_output(payload, args.output)
        print(f"Publicadas {payload['total_items']} novedades en {args.output}")
        try:
            with Storage(args.db or settings.database_path) as storage:
                run_id = storage.save_run(payload)
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
        report = validate_publications_payload(payload)
        _print_validation_report(report)
        if not report.ok:
            raise SystemExit(1)
        write_output(payload, args.output)
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
    elif args.command == "validate":
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        report = validate_publications_payload(payload)
        _print_validation_report(report)
        if not report.ok:
            raise SystemExit(1)
        print(f"Validación correcta: {payload['total_items']} novedades en {args.input}")


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
