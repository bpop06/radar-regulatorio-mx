#!/usr/bin/env python3
"""Verify that GitHub Pages serves the exact locally approved cut."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener, urlopen

import truststore

from app.url_policy import OfficialUrlError, redirect_target_allowed, validate_official_url

DEFAULT_BASE_URL = "https://bpop06.github.io/radar-regulatorio-mx/"
DEFAULT_WORKERS = 8
MAX_WORKERS = 32
SCHEMA_VERSION = 8
USER_AGENT = "Radar-Regulatorio-MX deployment verifier/8"


def _ssl_context() -> ssl.SSLContext:
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    intermediates = Path(__file__).resolve().parents[1] / "app/certs/intermediates.pem"
    if intermediates.is_file():
        context.load_verify_locations(cafile=str(intermediates))
    return context


SSL_CONTEXT = _ssl_context()


class SmokeError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    """Expose redirects so every hop can be checked against the same origin."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


_OFFICIAL_REDIRECT_CODES = {301, 302, 303, 307, 308}


def _fetch(base_url: str, path: str, *, timeout: float) -> bytes:
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    request = Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
    try:
        with urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
            if response.status != 200:
                raise SmokeError(f"{url}: HTTP {response.status}")
            return response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise SmokeError(f"{url}: {type(exc).__name__}: {exc}") from exc


def _json_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SmokeError(f"{label}: JSON inválido: {exc}") from exc
    if not isinstance(payload, dict):
        raise SmokeError(f"{label}: se esperaba un objeto JSON")
    return payload


def _load_expected(path: Path) -> dict[str, Any]:
    try:
        return _json_bytes(path.read_bytes(), label=f"manifest local {path}")
    except OSError as exc:
        raise SmokeError(f"manifest local {path}: no se pudo leer: {exc}") from exc


def _artifact_index(manifest: dict[str, Any], *, label: str) -> dict[str, dict[str, Any]]:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise SmokeError(f"{label}.schema_version debe ser {SCHEMA_VERSION}")
    cut_id = manifest.get("cut_id")
    if not isinstance(cut_id, str) or re.fullmatch(r"[0-9a-f]{24}", cut_id) is None:
        raise SmokeError(f"{label}.cut_id debe ser un digest hexadecimal de 24 caracteres")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise SmokeError(f"{label}.artifacts debe ser un objeto no vacío")
    if manifest.get("artifact_count") != len(artifacts):
        raise SmokeError(
            f"{label}.artifact_count={manifest.get('artifact_count')!r} "
            f"pero artifacts contiene {len(artifacts)} entradas"
        )

    validated: dict[str, dict[str, Any]] = {}
    for path, metadata in artifacts.items():
        if not isinstance(path, str) or not path:
            raise SmokeError(f"{label}: cada ruta de artefacto debe ser texto no vacío")
        parsed = urlsplit(path)
        relative = PurePosixPath(path)
        if (
            parsed.scheme
            or parsed.netloc
            or relative.is_absolute()
            or ".." in relative.parts
            or "\\" in path
        ):
            raise SmokeError(f"{label}: ruta de artefacto insegura: {path!r}")
        if not isinstance(metadata, dict):
            raise SmokeError(f"{label}.artifacts[{path!r}] debe ser un objeto")
        digest = metadata.get("sha256")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise SmokeError(f"{label}.artifacts[{path!r}].sha256 no es válido")
        byte_count = metadata.get("bytes")
        if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count < 0:
            raise SmokeError(f"{label}.artifacts[{path!r}].bytes no es válido")
        validated[path] = metadata

    required = {"data/edition.json", "data/publications.json"}
    missing = sorted(required - validated.keys())
    if missing:
        raise SmokeError(f"{label}: faltan artefactos requeridos: {', '.join(missing)}")
    return validated


def _manifest_difference(expected: dict[str, Any], actual: dict[str, Any]) -> str:
    differing = sorted(
        key
        for key in expected.keys() | actual.keys()
        if expected.get(key) != actual.get(key)
    )
    return ", ".join(differing) or "contenido"


def _verify_artifact(
    base_url: str,
    path: str,
    metadata: dict[str, Any],
    *,
    timeout: float,
) -> bytes:
    raw = _fetch(base_url, path, timeout=timeout)
    actual_size = len(raw)
    if actual_size != metadata["bytes"]:
        raise SmokeError(
            f"tamaño remoto {actual_size} bytes != {metadata['bytes']} bytes esperados"
        )
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != metadata["sha256"]:
        raise SmokeError(
            f"SHA-256 remoto {actual_hash} != {metadata['sha256']} esperado"
        )
    return raw


def _fetch_all_artifacts(
    base_url: str,
    artifacts: dict[str, dict[str, Any]],
    *,
    timeout: float,
    workers: int,
) -> dict[str, bytes]:
    if not 1 <= workers <= MAX_WORKERS:
        raise SmokeError(f"workers debe estar entre 1 y {MAX_WORKERS}")

    contents: dict[str, bytes] = {}
    failures: dict[str, str] = {}
    worker_count = min(workers, len(artifacts))
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="pages-smoke",
    ) as executor:
        pending = {
            executor.submit(
                _verify_artifact,
                base_url,
                path,
                metadata,
                timeout=timeout,
            ): path
            for path, metadata in artifacts.items()
        }
        for future in as_completed(pending):
            path = pending[future]
            try:
                contents[path] = future.result()
            except Exception as exc:  # La frontera agrega errores de todos los workers.
                failures[path] = str(exc)

    if failures:
        shown = sorted(failures.items())[:10]
        detail = "\n".join(f"- {path}: {message}" for path, message in shown)
        omitted = len(failures) - len(shown)
        suffix = f"\n- ... y {omitted} fallos más" if omitted else ""
        raise SmokeError(
            f"fallaron {len(failures)} de {len(artifacts)} artefactos:\n{detail}{suffix}"
        )
    return contents


def _decode_cut_artifacts(
    contents: dict[str, bytes],
    *,
    cut_id: str,
) -> dict[str, dict[str, Any]]:
    decoded: dict[str, dict[str, Any]] = {}
    mismatches: list[str] = []
    for path, raw in contents.items():
        if not path.endswith(".json"):
            continue
        payload = _json_bytes(raw, label=path)
        decoded[path] = payload
        if payload.get("cut_id") != cut_id:
            mismatches.append(f"{path}={payload.get('cut_id')!r}")
    if mismatches:
        shown = ", ".join(sorted(mismatches)[:10])
        suffix = f" y {len(mismatches) - 10} más" if len(mismatches) > 10 else ""
        raise SmokeError(f"cut_id inconsistente; se esperaba {cut_id}: {shown}{suffix}")
    return decoded


def _published_date(item: dict[str, Any], *, path: str) -> date:
    value = item.get("official_published_at") or item.get("published_at")
    if not isinstance(value, str):
        raise SmokeError(f"{path}: la ficha no tiene fecha oficial válida")
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise SmokeError(f"{path}: fecha oficial inválida: {value!r}") from exc


def _oldest_item(
    decoded: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any], date]:
    candidates: list[tuple[date, str, str, dict[str, Any]]] = []
    for path, envelope in decoded.items():
        if not path.startswith("data/items/") or not path.endswith(".json"):
            continue
        item = envelope.get("item")
        if not isinstance(item, dict):
            raise SmokeError(f"{path}: la ficha no contiene un objeto item")
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            raise SmokeError(f"{path}: la ficha no contiene un id válido")
        candidates.append((_published_date(item, path=path), item_id, path, item))
    if not candidates:
        raise SmokeError("el manifiesto no contiene fichas permanentes data/items/*.json")
    published, _item_id, path, item = min(candidates, key=lambda value: value[:3])
    return path, item, published


def _verify_oldest_history(
    decoded: dict[str, dict[str, Any]],
    contents: dict[str, bytes],
) -> dict[str, str]:
    item_path, item, published = _oldest_item(decoded)
    item_id = str(item["id"])
    note_path = item.get("detail_url")
    if not isinstance(note_path, str) or not note_path.startswith("notas/"):
        raise SmokeError(f"{item_path}: la ficha histórica más antigua no tiene detail_url estable")
    note = contents.get(note_path)
    if note is None:
        raise SmokeError(
            f"{item_path}: falta la nota {note_path} de la ficha histórica más antigua"
        )

    raw_official_url = item.get("canonical_url") or item.get("url")
    if not isinstance(raw_official_url, str):
        raise SmokeError(f"{item_path}: la ficha histórica más antigua no tiene URL oficial")
    escaped_url = html.escape(raw_official_url, quote=True).encode("utf-8")
    if raw_official_url.encode("utf-8") not in note and escaped_url not in note:
        raise SmokeError(
            f"{note_path}: la nota histórica más antigua no enlaza su fuente oficial"
        )
    try:
        official_url = validate_official_url(raw_official_url, resolve_dns=True)
    except OfficialUrlError as exc:
        raise SmokeError(
            f"{item_path}: la ficha histórica más antigua no tiene URL oficial segura"
        ) from exc

    archive_path = f"data/archive/{published:%Y-%m}.json"
    archive = decoded.get(archive_path)
    summaries = archive.get("items") if isinstance(archive, dict) else None
    if not isinstance(summaries, list) or not any(
        isinstance(summary, dict) and summary.get("id") == item_id for summary in summaries
    ):
        raise SmokeError(
            f"{archive_path}: no indexa la ficha histórica más antigua {item_id}"
        )

    return {
        "id": item_id,
        "official_published_at": published.isoformat(),
        "item_path": item_path,
        "note_path": note_path,
        "official_url": official_url,
    }


def _verify_official_link(url: str, *, timeout: float) -> None:
    try:
        original_url = validate_official_url(url, resolve_dns=True)
    except OfficialUrlError as exc:
        raise SmokeError(f"URL oficial insegura: {exc}") from exc
    current_url = original_url
    opener = build_opener(_NoRedirect(), HTTPSHandler(context=SSL_CONTEXT))
    for _ in range(6):
        request = Request(
            current_url,
            headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"},
        )
        try:
            with opener.open(request, timeout=timeout) as response:
                if not 200 <= response.status < 300:
                    raise SmokeError(f"{current_url}: HTTP {response.status}")
                response.read(1)
                return
        except HTTPError as exc:
            if exc.code not in _OFFICIAL_REDIRECT_CODES:
                raise SmokeError(
                    f"{current_url}: HTTPError: HTTP Error {exc.code}: {exc.reason}"
                ) from exc
            location = exc.headers.get("Location")
            if not location:
                raise SmokeError(f"{current_url}: redirección sin destino") from exc
            candidate = urljoin(current_url, location)
            if not redirect_target_allowed(original_url, candidate, resolve_dns=True):
                raise SmokeError(
                    f"{current_url}: redirección fuera del origen oficial permitido"
                ) from exc
            try:
                current_url = validate_official_url(candidate, resolve_dns=True)
            except OfficialUrlError as validation_error:
                raise SmokeError(
                    f"{current_url}: destino oficial inseguro: {validation_error}"
                ) from validation_error
        except (URLError, TimeoutError, OSError) as exc:
            raise SmokeError(f"{current_url}: {type(exc).__name__}: {exc}") from exc
    raise SmokeError(f"{original_url}: demasiadas redirecciones oficiales")


def verify(
    base_url: str,
    expected_path: Path,
    timeout: float,
    *,
    workers: int = DEFAULT_WORKERS,
) -> dict[str, Any]:
    if timeout <= 0:
        raise SmokeError("timeout debe ser mayor que cero")
    started = time.monotonic()
    expected = _load_expected(expected_path)
    artifacts = _artifact_index(expected, label="manifest local")

    index = _fetch(base_url, "index.html", timeout=timeout)
    if not index.strip():
        raise SmokeError("index.html remoto está vacío")
    actual = _json_bytes(
        _fetch(base_url, "data/manifest.json", timeout=timeout),
        label="manifest remoto",
    )
    _artifact_index(actual, label="manifest remoto")
    if actual != expected:
        raise SmokeError(
            "manifest remoto no coincide exactamente con el aprobado; "
            f"campos distintos: {_manifest_difference(expected, actual)}"
        )

    contents = _fetch_all_artifacts(
        base_url,
        artifacts,
        timeout=timeout,
        workers=workers,
    )
    cut_id = str(expected["cut_id"])
    decoded = _decode_cut_artifacts(contents, cut_id=cut_id)
    oldest = _verify_oldest_history(decoded, contents)
    _verify_official_link(oldest["official_url"], timeout=timeout)

    return {
        "base_url": base_url,
        "schema_version": expected["schema_version"],
        "cut_id": cut_id,
        "verified_artifact_count": len(contents),
        "workers": min(workers, len(artifacts)),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "oldest_historical_item": oldest,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--expected",
        type=Path,
        default=Path("docs/data/manifest.json"),
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args()
    try:
        result = verify(
            args.base_url,
            args.expected,
            args.timeout,
            workers=args.workers,
        )
    except (json.JSONDecodeError, SmokeError) as exc:
        print(f"Deployment smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
