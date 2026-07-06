from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.config import Settings
from app.pipeline import collect, write_output
from app.validation import ValidationReport, validate_publications_payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recolecta novedades regulatorias mexicanas")
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument(
        "--output",
        type=Path,
        default=Path("site/data/publications.json"),
    )
    collect_parser.add_argument("--days", type=int)
    collect_parser.add_argument("--dry-run", action="store_true")
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument(
        "--input",
        type=Path,
        default=Path("site/data/publications.json"),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "collect":
        payload = asyncio.run(collect(Settings(), args.days))
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
