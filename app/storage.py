from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.locking import private_state_lock

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

CREATE TABLE IF NOT EXISTS extraction_cache_v2 (
    source_url TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(source_url, extractor_version, evidence_hash)
);

CREATE TABLE IF NOT EXISTS cut_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    p50_seconds REAL NOT NULL,
    p95_seconds REAL NOT NULL,
    cache_hits INTEGER NOT NULL,
    ocr_documents INTEGER NOT NULL,
    failures INTEGER NOT NULL,
    tokens INTEGER,
    retained_items INTEGER NOT NULL,
    total_items INTEGER NOT NULL,
    payload TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class StorageReport:
    database_path: str
    size_bytes: int
    sidecar_bytes: int
    state_max_bytes: int
    runs: int
    documents: int
    cache_entries: int
    cache_bytes: int
    last_generated_at: str | None


class StorageCapacityError(RuntimeError):
    """The private evidence store reached its fail-closed high-water mark."""


class Storage:
    """Memoria histórica local del radar. La base acumula corridas y
    documentos deduplicados por id; el JSON público sigue siendo el contrato
    de publicación y se puede regenerar desde aquí con `export_payload`."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        max_bytes: int | None = None,
        lock_wait_seconds: float = 30.0,
    ) -> None:
        self.path = Path(database_path).expanduser().resolve()
        parent = self.path.parent
        repository_root = Path(__file__).resolve().parents[1]
        if (
            parent == Path(parent.anchor)
            or parent == Path.cwd().resolve()
            or self.path == repository_root
            or repository_root in self.path.parents
        ):
            raise ValueError("la base privada requiere un directorio dedicado")
        created_parent = not parent.exists()
        parent.mkdir(parents=True, exist_ok=True)
        # No cambiar permisos de directorios preexistentes como /tmp o el
        # repositorio. Sólo endurecemos un directorio que acabamos de crear.
        if created_parent:
            parent.chmod(0o700)
        configured_max = max_bytes if max_bytes is not None else int(
            os.getenv("RADAR_STATE_MAX_BYTES", str(512 * 1024 * 1024))
        )
        if configured_max <= 0:
            raise ValueError("RADAR_STATE_MAX_BYTES debe ser positivo")
        self.max_bytes = configured_max
        self._lock = (
            nullcontext()
            if os.getenv("RADAR_CUT_LOCK_HELD") == "1"
            else private_state_lock(parent, wait_seconds=lock_wait_seconds)
        )
        self._lock.__enter__()
        self._closed = False
        try:
            self._connection = sqlite3.connect(self.path, timeout=5.0)
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.executescript(SCHEMA)
            os.chmod(self.path, 0o600)
        except Exception:
            self._lock.__exit__(None, None, None)
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._connection.close()
        finally:
            self._lock.__exit__(None, None, None)

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def save_run(self, payload: dict[str, Any]) -> int:
        self.ensure_capacity(_json_size(payload))
        cursor = self._connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
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
                item = dict(item)
                previous = cursor.execute(
                    "SELECT payload FROM documents WHERE id = ?", (item["id"],)
                ).fetchone()
                if previous is not None:
                    previous_item = json.loads(previous[0])
                    item.setdefault("first_seen_at", previous_item.get("first_seen_at"))
                    same_identity = (
                        item.get("canonical_url") or item.get("url")
                    ) == (previous_item.get("canonical_url") or previous_item.get("url"))
                    if same_identity:
                        _preserve_private_analysis(item, previous_item)
                        # El análisis se conserva para poder reutilizarlo por SHA,
                        # pero un corte recién recolectado no queda publicable hasta
                        # que la fuente oficial se revalide en esta ejecución.
                        if previous_item.get("extraction_status") == "complete":
                            item["source_revalidation_status"] = "pending"
                item.setdefault("first_seen_at", payload["generated_at"])
                item["last_seen_at"] = payload["generated_at"]
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
        except Exception:
            self._connection.rollback()
            raise
        return run_id

    def export_payload(self) -> dict[str, Any]:
        """Reconstruye el payload público con exactamente los documentos de la
        última corrida guardada (los vistos en corridas anteriores pero no en
        la última no se incluyen)."""
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

    def update_document_fields(self, document_id: str, fields: dict[str, Any]) -> bool:
        """Actualiza campos puntuales del payload de un documento existente.
        Devuelve False si el documento no está en la base."""
        cursor = self._connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            row = cursor.execute(
                "SELECT payload FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if row is None:
                self._connection.rollback()
                return False
            document = json.loads(row[0])
            document.update(fields)
            serialized = json.dumps(document, ensure_ascii=False)
            self.ensure_capacity(len(serialized.encode("utf-8")))
            cursor.execute(
                "UPDATE documents SET payload = ? WHERE id = ?",
                (serialized, document_id),
            )
            self._connection.commit()
            return True
        except Exception:
            self._connection.rollback()
            raise

    def update_document_fields_atomic(
        self,
        updates: dict[str, dict[str, Any]],
        *,
        expected_source_hashes: dict[str, str] | None = None,
        expected_extraction_contracts: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Aplica un lote completo o revierte todo ante un id inexistente."""
        cursor = self._connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            for document_id, fields in updates.items():
                row = cursor.execute(
                    "SELECT payload FROM documents WHERE id = ?", (document_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(document_id)
                document = json.loads(row[0])
                if expected_source_hashes is not None and document.get(
                    "source_content_hash"
                ) != expected_source_hashes.get(document_id):
                    raise ValueError(
                        f"la fuente cambió durante la aplicación: {document_id}"
                    )
                if expected_extraction_contracts is not None:
                    expected = expected_extraction_contracts.get(document_id, {})
                    actual = {
                        name: document.get(name)
                        for name in (
                            "extraction_status",
                            "source_revalidation_status",
                            "extraction_version",
                            "extraction_method",
                            "source_sections",
                        )
                    }
                    if actual != expected:
                        raise ValueError(
                            f"la extracción cambió durante la aplicación: {document_id}"
                        )
                document.update(fields)
                serialized = json.dumps(document, ensure_ascii=False)
                self.ensure_capacity(len(serialized.encode("utf-8")))
                cursor.execute(
                    "UPDATE documents SET payload = ? WHERE id = ?",
                    (serialized, document_id),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def pending_editorial_items(self) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT payload FROM documents ORDER BY published_at DESC"
        ).fetchall()
        items = [json.loads(row[0]) for row in rows]
        return [
            item
            for item in items
            if item.get("extraction_status") != "complete"
            or item.get("editorial_status") != "complete"
        ]

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT payload FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def all_documents(self) -> list[dict[str, Any]]:
        """Devuelve el archivo privado completo, no sólo el último corte.

        La cola editorial abarca documentos históricos; su aplicación debe
        resolver exactamente el mismo universo sin reintroducirlos como
        novedades de la corrida vigente.
        """
        rows = self._connection.execute(
            "SELECT payload FROM documents ORDER BY published_at DESC, id"
        ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_extraction(
        self, source_url: str, extractor_version: str, evidence_hash: str = ""
    ) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT payload FROM extraction_cache_v2"
            " WHERE source_url = ? AND extractor_version = ? AND evidence_hash = ?",
            (source_url, extractor_version, evidence_hash),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def get_latest_extraction(
        self, source_url: str, extractor_version: str
    ) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT payload FROM extraction_cache_v2"
            " WHERE source_url = ? AND extractor_version = ? AND status = 'complete'"
            " ORDER BY updated_at DESC LIMIT 1",
            (source_url, extractor_version),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put_extraction(
        self, result: dict[str, Any], extractor_version: str, evidence_hash: str = ""
    ) -> None:
        self.put_extractions([(result, extractor_version, evidence_hash)])

    def put_extractions(
        self, entries: Iterable[tuple[dict[str, Any], str, str]]
    ) -> None:
        """Persiste una tanda de caché en una única transacción exclusiva."""
        prepared: list[tuple[str, str, str, str, str, str]] = []
        for result, extractor_version, evidence_hash in entries:
            source_url = str(result.get("source_url") or "")
            source_hash = str(result.get("content_hash") or "")
            if not source_url:
                raise ValueError("la extracción no contiene source_url")
            cached = dict(result)
            cached["cache_hit"] = False
            prepared.append(
                (
                    source_url,
                    extractor_version,
                    evidence_hash,
                    source_hash,
                    str(result.get("status") or "failed"),
                    json.dumps(cached, ensure_ascii=False),
                )
            )
        if not prepared:
            return
        self.ensure_capacity(sum(len(entry[-1].encode("utf-8")) for entry in prepared))
        cursor = self._connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.executemany(
                "INSERT INTO extraction_cache_v2"
                " (source_url, extractor_version, evidence_hash, source_hash, status, payload)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(source_url, extractor_version, evidence_hash) DO UPDATE SET"
                " source_hash=excluded.source_hash, status=excluded.status,"
                " payload=excluded.payload, updated_at=CURRENT_TIMESTAMP",
                prepared,
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def ensure_capacity(self, additional_bytes: int = 0) -> None:
        """Falla antes de ampliar el estado privado sobre su máximo explícito."""
        if additional_bytes < 0:
            raise ValueError("additional_bytes no puede ser negativo")
        current = self._state_size_bytes()
        overhead = max(4_096, min(65_536, additional_bytes // 10))
        if current + additional_bytes + overhead > self.max_bytes:
            raise StorageCapacityError(
                "el estado privado alcanzaría "
                f"{current + additional_bytes + overhead} bytes; máximo "
                f"configurado: {self.max_bytes}"
            )

    def _state_size_bytes(self) -> int:
        return sum(
            candidate.stat().st_size
            for candidate in _state_files(self.path)
            if candidate.exists() and candidate.is_file()
        )

    def _cache_stats(self, cursor: sqlite3.Cursor) -> tuple[int, int]:
        entries, payload_bytes = cursor.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(payload)), 0)"
            " FROM extraction_cache_v2"
        ).fetchone()
        return int(entries), int(payload_bytes)

    def report(self) -> StorageReport:
        cursor = self._connection.cursor()
        runs = cursor.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0]
        documents = cursor.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        cache_entries, cache_bytes = self._cache_stats(cursor)
        last = cursor.execute(
            "SELECT generated_at FROM collection_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        size = self.path.stat().st_size if self.path.exists() else 0
        total = self._state_size_bytes()
        return StorageReport(
            database_path=str(self.path),
            size_bytes=size,
            sidecar_bytes=max(0, total - size),
            state_max_bytes=self.max_bytes,
            runs=runs,
            documents=documents,
            cache_entries=cache_entries,
            cache_bytes=cache_bytes,
            last_generated_at=last[0] if last else None,
        )

    def save_cut_metrics(self, metrics: dict[str, Any]) -> None:
        serialized = json.dumps(metrics, ensure_ascii=False)
        self.ensure_capacity(len(serialized.encode("utf-8")))
        cursor = self._connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO cut_metrics"
                " (generated_at, duration_seconds, p50_seconds, p95_seconds,"
                " cache_hits, ocr_documents, failures, tokens, retained_items,"
                " total_items, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    metrics["generated_at"], metrics["duration_seconds"],
                    metrics["p50_seconds"], metrics["p95_seconds"],
                    metrics["cache_hits"], metrics["ocr_documents"],
                    metrics["failures"], metrics.get("tokens"),
                    metrics["retained_items"], metrics["total_items"], serialized,
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def latest_cut_metrics(self) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT payload FROM cut_metrics ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return json.loads(row[0]) if row else None

    def add_editorial_tokens(self, tokens: int) -> None:
        """Acumula uso reportado por agentes en las métricas del corte vigente."""
        if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
            raise ValueError("tokens debe ser un entero no negativo")
        cursor = self._connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            row = cursor.execute(
                "SELECT id, payload FROM cut_metrics ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                self._connection.rollback()
                return
            payload = json.loads(row[1])
            payload["tokens"] = int(payload.get("tokens") or 0) + tokens
            serialized = json.dumps(payload, ensure_ascii=False)
            self.ensure_capacity(len(serialized.encode("utf-8")))
            cursor.execute(
                "UPDATE cut_metrics SET tokens = ?, payload = ? WHERE id = ?",
                (payload["tokens"], serialized, row[0]),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

def _state_files(database_path: Path) -> tuple[Path, ...]:
    return (
        database_path,
        database_path.with_name(f"{database_path.name}-wal"),
        database_path.with_name(f"{database_path.name}-shm"),
        database_path.with_name(f"{database_path.name}-journal"),
    )


def _json_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


_PRESERVED_EXTRACTION_FIELDS = (
    "extraction_status", "source_content_hash", "extraction_method",
    "extraction_diagnostics", "extracted_text", "source_sections",
    "extraction_retrieved_at", "extraction_version",
)
_PRESERVED_EDITORIAL_FIELDS = (
    "title", "summary_teaser", "summary", "card_body", "detail_markdown",
    "detail_data_url", "executive_summary", "detailed_summary", "impacts",
    "recommended_actions", "evidence", "coverage", "editorial_status",
    "review_reason", "ai_generated",
)


def _preserve_private_analysis(item: dict[str, Any], previous: dict[str, Any]) -> None:
    """Conserva análisis por fuente hasta revalidar el SHA-256 real de sus bytes."""
    for field in _PRESERVED_EXTRACTION_FIELDS:
        if field in previous:
            item[field] = previous[field]
    if previous.get("editorial_status") == "complete":
        for field in _PRESERVED_EDITORIAL_FIELDS:
            if field in previous:
                item[field] = previous[field]
