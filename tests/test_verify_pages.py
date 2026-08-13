from __future__ import annotations

import hashlib
import html
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from scripts.verify_pages import SmokeError, _oldest_item, verify

CUT_ID = "a" * 24


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()


class _FixtureServer(ThreadingHTTPServer):
    routes: dict[str, tuple[int, bytes]]
    requests: list[str]
    request_lock: threading.Lock


class _FixtureHandler(BaseHTTPRequestHandler):
    server: _FixtureServer

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        with self.server.request_lock:
            self.server.requests.append(self.path)
        status, body = self.server.routes.get(path, (404, b"not found"))
        self.send_response(status)
        content_type = "application/json" if path.endswith(".json") else "text/html"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _serve() -> Iterator[tuple[_FixtureServer, str]]:
    server = _FixtureServer(("127.0.0.1", 0), _FixtureHandler)
    server.routes = {}
    server.requests = []
    server.request_lock = threading.Lock()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield server, f"http://{host}:{port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _install_site(
    server: _FixtureServer,
    base_url: str,
    expected_path: Path,
    *,
    old_note_has_official_link: bool = True,
    artifact_cut_id: str = CUT_ID,
) -> dict[str, object]:
    old_official = f"{base_url}official?case=old&language=es"
    new_official = f"{base_url}official?case=new"
    old_item = {
        "id": "source:old",
        "official_published_at": "1999-12-31",
        "detail_url": "notas/old.html",
        "canonical_url": old_official,
    }
    new_item = {
        "id": "source:new",
        "official_published_at": "2026-08-12",
        "detail_url": "notas/new.html",
        "canonical_url": new_official,
    }
    old_link = html.escape(old_official, quote=True) if old_note_has_official_link else ""
    payloads = {
        "data/edition.json": _json_bytes(
            {"schema_version": 8, "cut_id": artifact_cut_id, "items": []}
        ),
        "data/publications.json": _json_bytes(
            {"schema_version": 8, "cut_id": artifact_cut_id, "items": []}
        ),
        "data/archive/1999-12.json": _json_bytes(
            {
                "schema_version": 8,
                "cut_id": artifact_cut_id,
                "month": "1999-12",
                "items": [{"id": old_item["id"]}],
            }
        ),
        "data/archive/2026-08.json": _json_bytes(
            {
                "schema_version": 8,
                "cut_id": artifact_cut_id,
                "month": "2026-08",
                "items": [{"id": new_item["id"]}],
            }
        ),
        "data/items/old.json": _json_bytes(
            {"schema_version": 8, "cut_id": artifact_cut_id, "item": old_item}
        ),
        "data/items/new.json": _json_bytes(
            {"schema_version": 8, "cut_id": artifact_cut_id, "item": new_item}
        ),
        "notas/old.html": f'<a href="{old_link}">Fuente oficial</a>'.encode(),
        "notas/new.html": f'<a href="{html.escape(new_official, quote=True)}">Fuente</a>'.encode(),
    }
    artifacts = {
        path: {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
        for path, body in payloads.items()
    }
    manifest: dict[str, object] = {
        "schema_version": 8,
        "cut_id": CUT_ID,
        "generated_at": "2026-08-13T03:50:09+00:00",
        "edition_date": "2026-08-12",
        "archive_months": ["1999-12", "2026-08"],
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    manifest_bytes = _json_bytes(manifest)
    expected_path.write_bytes(manifest_bytes)
    server.routes = {
        "/index.html": (200, b"<!doctype html><title>Radar</title>"),
        "/data/manifest.json": (200, manifest_bytes),
        "/official": (200, b"official source"),
        **{f"/{path}": (200, body) for path, body in payloads.items()},
    }
    return manifest


def test_verify_fetches_and_hashes_every_artifact_and_oldest_history(
    tmp_path: Path,
) -> None:
    with _serve() as (server, base_url):
        expected = tmp_path / "manifest.json"
        manifest = _install_site(server, base_url, expected)

        result = verify(base_url, expected, timeout=2, workers=3)

        artifact_paths = set(manifest["artifacts"])
        requested_paths = {urlsplit(request).path.lstrip("/") for request in server.requests}
        assert artifact_paths <= requested_paths
        assert result["verified_artifact_count"] == len(artifact_paths)
        assert result["cut_id"] == CUT_ID
        assert result["workers"] == 3
        assert result["oldest_historical_item"] == {
            "id": "source:old",
            "official_published_at": "1999-12-31",
            "item_path": "data/items/old.json",
            "note_path": "notas/old.html",
            "official_url": f"{base_url}official?case=old&language=es",
        }
        assert "index.html" in requested_paths
        assert "data/manifest.json" in requested_paths
        assert any(request.startswith("/official?case=old") for request in server.requests)


def test_verify_rejects_a_tampered_artifact(tmp_path: Path) -> None:
    with _serve() as (server, base_url):
        expected = tmp_path / "manifest.json"
        _install_site(server, base_url, expected)
        _status, original = server.routes["/data/items/new.json"]
        server.routes["/data/items/new.json"] = (200, b"[" + original[1:])

        with pytest.raises(SmokeError, match=r"data/items/new\.json: .*SHA-256"):
            verify(base_url, expected, timeout=2, workers=2)


def test_verify_reports_a_missing_artifact(tmp_path: Path) -> None:
    with _serve() as (server, base_url):
        expected = tmp_path / "manifest.json"
        _install_site(server, base_url, expected)
        server.routes.pop("/notas/new.html")

        with pytest.raises(SmokeError, match=r"notas/new\.html: .*HTTP Error 404"):
            verify(base_url, expected, timeout=2, workers=2)


def test_oldest_selection_uses_official_date_then_stable_id() -> None:
    decoded = {
        "data/items/later.json": {
            "item": {"id": "source:later", "official_published_at": "2020-01-01"}
        },
        "data/items/tie-z.json": {
            "item": {"id": "source:z", "published_at": "1999-12-31"}
        },
        "data/items/tie-a.json": {
            "item": {"id": "source:a", "official_published_at": "1999-12-31"}
        },
    }

    path, item, published = _oldest_item(decoded)

    assert path == "data/items/tie-a.json"
    assert item["id"] == "source:a"
    assert published.isoformat() == "1999-12-31"


def test_verify_rejects_remote_manifest_mismatch_before_artifacts(tmp_path: Path) -> None:
    with _serve() as (server, base_url):
        expected = tmp_path / "manifest.json"
        manifest = _install_site(server, base_url, expected)
        changed = {**manifest, "generated_at": "2026-08-13T04:00:00+00:00"}
        server.routes["/data/manifest.json"] = (200, _json_bytes(changed))

        with pytest.raises(
            SmokeError,
            match=r"manifest remoto no coincide exactamente.*generated_at",
        ):
            verify(base_url, expected, timeout=2, workers=2)

        requested_paths = {urlsplit(request).path for request in server.requests}
        assert "/data/items/old.json" not in requested_paths


def test_verify_rejects_an_artifact_cut_id_mismatch(tmp_path: Path) -> None:
    with _serve() as (server, base_url):
        expected = tmp_path / "manifest.json"
        _install_site(server, base_url, expected, artifact_cut_id="b" * 24)

        with pytest.raises(SmokeError, match=r"cut_id inconsistente.*data/edition.json"):
            verify(base_url, expected, timeout=2, workers=2)


def test_verify_requires_oldest_note_to_link_official_source(tmp_path: Path) -> None:
    with _serve() as (server, base_url):
        expected = tmp_path / "manifest.json"
        _install_site(server, base_url, expected, old_note_has_official_link=False)

        with pytest.raises(
            SmokeError,
            match=r"notas/old\.html: la nota histórica más antigua no enlaza",
        ):
            verify(base_url, expected, timeout=2, workers=2)
