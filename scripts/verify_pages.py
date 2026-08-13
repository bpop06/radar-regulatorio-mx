#!/usr/bin/env python3
"""Verify that GitHub Pages serves the exact locally approved cut."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://bpop06.github.io/radar-regulatorio-mx/"
USER_AGENT = "Radar-Regulatorio-MX deployment verifier/8"


class SmokeError(RuntimeError):
    pass


def _fetch(base_url: str, path: str, *, timeout: float) -> bytes:
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    request = Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise SmokeError(f"{url}: HTTP {response.status}")
            return response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise SmokeError(f"{url}: {type(exc).__name__}: {exc}") from exc


def _json_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SmokeError(f"{label}: JSON inválido: {exc}") from exc
    if not isinstance(payload, dict):
        raise SmokeError(f"{label}: se esperaba un objeto JSON")
    return payload


def _sample_paths(manifest: dict[str, Any]) -> list[str]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise SmokeError("manifest.artifacts no es un objeto")

    selected = ["data/edition.json", "data/publications.json"]
    for prefix in ("data/archive/", "data/items/", "notas/"):
        matches = sorted(path for path in artifacts if str(path).startswith(prefix))
        if matches:
            selected.append(matches[-1] if prefix == "data/archive/" else matches[0])
    return list(dict.fromkeys(path for path in selected if path in artifacts))


def _verify_official_link(item: dict[str, Any], *, timeout: float) -> str:
    value = item.get("canonical_url") or item.get("url")
    if not isinstance(value, str) or urlsplit(value).scheme not in {"http", "https"}:
        raise SmokeError("la ficha de muestra no contiene una URL oficial http(s)")
    request = Request(
        value,
        headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            if not 200 <= response.status < 400:
                raise SmokeError(f"{value}: HTTP {response.status}")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise SmokeError(f"{value}: {type(exc).__name__}: {exc}") from exc
    return value


def verify(base_url: str, expected_path: Path, timeout: float) -> dict[str, Any]:
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    if not isinstance(expected, dict):
        raise SmokeError("el manifiesto local no es un objeto")

    _fetch(base_url, "index.html", timeout=timeout)
    actual = _json_bytes(
        _fetch(base_url, "data/manifest.json", timeout=timeout),
        label="manifest remoto",
    )
    for key in ("schema_version", "cut_id", "artifacts"):
        if actual.get(key) != expected.get(key):
            raise SmokeError(f"manifest remoto no coincide en {key}")

    verified: list[str] = []
    sample_item: dict[str, Any] | None = None
    artifacts = expected["artifacts"]
    for path in _sample_paths(expected):
        raw = _fetch(base_url, path, timeout=timeout)
        expected_hash = artifacts[path].get("sha256")
        actual_hash = hashlib.sha256(raw).hexdigest()
        if actual_hash != expected_hash:
            raise SmokeError(f"{path}: hash remoto {actual_hash} != {expected_hash}")
        verified.append(path)
        if path.startswith("data/items/"):
            sample_item = _json_bytes(raw, label=path)

    official_url = ""
    if sample_item is not None:
        item = sample_item.get("item", sample_item)
        if not isinstance(item, dict):
            raise SmokeError("la ficha de muestra no contiene item")
        official_url = _verify_official_link(item, timeout=timeout)

    return {
        "cut_id": expected.get("cut_id"),
        "verified_artifacts": verified,
        "official_url": official_url,
        "base_url": base_url,
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
    args = parser.parse_args()
    try:
        result = verify(args.base_url, args.expected, args.timeout)
    except (OSError, json.JSONDecodeError, SmokeError) as exc:
        print(f"Deployment smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
