from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.config import Settings
from app.pipeline import collect, write_output


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
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "collect":
        payload = asyncio.run(collect(Settings(), args.days))
        if args.dry_run:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            write_output(payload, args.output)
            print(f"Publicadas {payload['total_items']} novedades en {args.output}")


if __name__ == "__main__":
    main()
