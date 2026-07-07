from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,
    lookback_days INTEGER NOT NULL,
    total_items INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS source_status (
    run_id INTEGER NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    items_found INTEGER NOT NULL,
    attempts INTEGER NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    published_at TEXT NOT NULL,
    first_seen_run INTEGER NOT NULL REFERENCES collection_runs(id),
    last_seen_run INTEGER NOT NULL REFERENCES collection_runs(id),
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_published_at
    ON documents(published_at);
"""


@dataclass(frozen=True)
class StorageReport:
    database_path: str
    size_bytes: int
    runs: int
    documents: int
    last_generated_at: str | None


class Storage:
    """Memoria histórica local del radar. La base acumula corridas y
    documentos deduplicados por id; el JSON público sigue siendo el contrato
    de publicación y se puede regenerar desde aquí con `export_payload`."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.executescript(SCHEMA)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def save_run(self, payload: dict[str, Any]) -> int:
        cursor = self._connection.cursor()
        cursor.execute(
            "INSERT INTO collection_runs (generated_at, lookback_days, total_items)"
            " VALUES (?, ?, ?)",
            (
                payload["generated_at"],
                payload["lookback_days"],
                payload["total_items"],
            ),
        )
        run_id = int(cursor.lastrowid or 0)

        for source in payload.get("sources", []):
            cursor.execute(
                "INSERT INTO source_status"
                " (run_id, source, status, items_found, attempts, error)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    source.get("source", ""),
                    source.get("status", ""),
                    source.get("items_found", 0),
                    source.get("attempts", 1),
                    source.get("error"),
                ),
            )

        for item in payload.get("items", []):
            cursor.execute(
                "INSERT INTO documents (id, published_at, first_seen_run,"
                " last_seen_run, payload) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET"
                " published_at = excluded.published_at,"
                " last_seen_run = excluded.last_seen_run,"
                " payload = excluded.payload",
                (
                    item["id"],
                    item["published_at"],
                    run_id,
                    run_id,
                    json.dumps(item, ensure_ascii=False),
                ),
            )

        self._connection.commit()
        return run_id

    def export_payload(self) -> dict[str, Any]:
        """Reconstruye el payload público desde la última corrida guardada,
        con los documentos vigentes dentro de su ventana de lookback."""
        cursor = self._connection.cursor()
        run = cursor.execute(
            "SELECT id, generated_at, lookback_days FROM collection_runs"
            " ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if run is None:
            raise LookupError("no hay corridas guardadas en la base local")
        run_id, generated_at, lookback_days = run

        sources = [
            {
                "source": row[0],
                "status": row[1],
                "items_found": row[2],
                "attempts": row[3],
                "error": row[4],
            }
            for row in cursor.execute(
                "SELECT source, status, items_found, attempts, error"
                " FROM source_status WHERE run_id = ?",
                (run_id,),
            )
        ]

        items = [
            json.loads(row[0])
            for row in cursor.execute(
                "SELECT payload FROM documents WHERE last_seen_run = ?"
                " ORDER BY published_at DESC",
                (run_id,),
            )
        ]

        return {
            "generated_at": generated_at,
            "lookback_days": lookback_days,
            "total_items": len(items),
            "sources": sources,
            "items": items,
        }

    def report(self) -> StorageReport:
        cursor = self._connection.cursor()
        runs = cursor.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0]
        documents = cursor.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        last = cursor.execute(
            "SELECT generated_at FROM collection_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        size = self.path.stat().st_size if self.path.exists() else 0
        return StorageReport(
            database_path=str(self.path),
            size_bytes=size,
            runs=runs,
            documents=documents,
            last_generated_at=last[0] if last else None,
        )
