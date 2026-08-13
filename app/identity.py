from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

SOURCE_PRIORITY = {
    "DOF": 400,
    "IMPI": 400,
    "SNICE": 300,
    "Gob.mx APF": 100,
}


def canonical_identity_url(value: str, *, source: str = "") -> str:
    """Normaliza una URL para identidad, conservando anclas parlamentarias."""

    parts = urlsplit(value.strip())
    query = sorted(
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"fbclid", "gclid"}
    )
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    fragment = parts.fragment if source.strip().casefold() == "diputados" else ""
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), fragment)
    )


def record_identity_url(record: Any) -> str:
    source = _value(record, "source")
    raw = _value(record, "canonical_url") or _value(record, "url")
    # canonical_url v8 no conserva fragmentos; Diputados requiere la URL
    # exacta para distinguir asuntos de una misma Gaceta.
    if source.casefold() == "diputados":
        raw = _value(record, "url") or raw
    return canonical_identity_url(raw, source=source)


def official_semantic_alias(record: Any) -> str:
    """Equivalencia oficial acotada de rutas prensa/artículos de Gob.mx IMPI."""

    raw = _value(record, "canonical_url") or _value(record, "url")
    parts = urlsplit(raw)
    host = parts.netloc.casefold().split(":", 1)[0]
    if host not in {"gob.mx", "www.gob.mx"}:
        return ""
    path = unquote(parts.path).strip("/")
    match = re.fullmatch(
        r"impi/(?:es/)?(?:prensa|articulos)/(?P<slug>[^/]+)",
        path,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    slug = re.sub(r"-\d{5,}$", "", match.group("slug").casefold())
    return f"gob.mx/impi/{slug}"


def records_are_aliases(left: Any, right: Any) -> bool:
    """Compara copias oficiales sin usar título ni fecha como identidad."""

    left_source = _value(left, "source").casefold()
    right_source = _value(right, "source").casefold()
    left_source_id = _value(left, "source_id")
    right_source_id = _value(right, "source_id")
    if left_source == right_source and left_source_id == right_source_id:
        return True

    left_semantic = official_semantic_alias(left)
    right_semantic = official_semantic_alias(right)
    if left_semantic and left_semantic == right_semantic:
        return True

    # Dos identificadores oficiales distintos de una misma fuente no se
    # fusionan sólo porque el proveedor haya reutilizado una URL genérica.
    if left_source == right_source:
        return False
    left_url = record_identity_url(left)
    return bool(left_url and left_url == record_identity_url(right))


def source_priority(source: str) -> int:
    return SOURCE_PRIORITY.get(source, 250)


def _value(record: Any, field: str) -> str:
    value = record.get(field) if isinstance(record, dict) else getattr(record, field, "")
    return str(value or "").strip()
