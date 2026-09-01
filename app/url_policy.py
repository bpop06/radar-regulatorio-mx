"""Fail-closed handling for links that claim to be official sources.

Collectors receive URLs from HTML, RSS and public JSON.  Treating any absolute
``href`` as authoritative would turn a compromised or malformed source page
into an outbound fetch primitive.  The helpers in this module make the policy
explicit and reusable by collectors, extractors and deployment verification.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Collection
from urllib.parse import urljoin, urlsplit, urlunsplit


class OfficialUrlError(ValueError):
    """A candidate link is not an allowed public HTTPS official URL."""


def normalize_host(value: str) -> str:
    return value.casefold().rstrip(".")


def resolve_public_addresses(
    host: str, port: int = 443
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    """Resolve a host and reject mixed, private, loopback or reserved DNS."""

    try:
        addresses = {
            ipaddress.ip_address(record[4][0])
            for record in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as exc:
        raise OfficialUrlError("no se pudo resolver el host oficial") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise OfficialUrlError("el host oficial resuelve a una red no permitida")
    return tuple(sorted(addresses, key=str))


def validate_official_url(
    value: str,
    *,
    allowed_hosts: Collection[str] | None = None,
    resolve_dns: bool = False,
) -> str:
    """Return a normalized HTTPS URL after enforcing host and network policy.

    ``allowed_hosts`` is an allowlist for a source-specific collector.  Without
    one, callers still receive the HTTPS/no-userinfo/public-network baseline.
    Fragments are intentionally discarded because they do not identify a
    remotely fetched resource and would make cache identity unstable.
    """

    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise OfficialUrlError("URL oficial inválida") from exc
    if parts.scheme.casefold() != "https" or not parts.hostname:
        raise OfficialUrlError("la fuente debe usar HTTPS")
    if parts.username is not None or parts.password is not None:
        raise OfficialUrlError("la URL oficial no puede incluir credenciales")
    if port not in (None, 443):
        raise OfficialUrlError("la URL oficial usa un puerto no permitido")

    host = normalize_host(parts.hostname)
    if host == "localhost" or host.endswith(".local"):
        raise OfficialUrlError("host de fuente no permitido")
    if allowed_hosts is not None:
        normalized_allowed = {normalize_host(candidate) for candidate in allowed_hosts}
        if host not in normalized_allowed:
            raise OfficialUrlError("host fuera de la política de la fuente")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if resolve_dns:
            resolve_public_addresses(host, 443)
    else:
        if not address.is_global:
            raise OfficialUrlError("dirección privada o reservada no permitida")

    return urlunsplit(("https", host, parts.path or "/", parts.query, ""))


def resolve_official_link(
    base_url: str,
    href: str,
    *,
    allowed_hosts: Collection[str] | None = None,
) -> str:
    """Resolve an HTML/RSS link and require it to remain in the source policy."""

    if not isinstance(href, str) or not href.strip():
        raise OfficialUrlError("enlace oficial vacío")
    try:
        base = validate_official_url(base_url)
        return validate_official_url(
            urljoin(base, href),
            allowed_hosts=allowed_hosts or {urlsplit(base).hostname or ""},
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, OfficialUrlError):
            raise
        raise OfficialUrlError("enlace oficial inválido") from exc


def redirect_target_allowed(
    original_url: str,
    target_url: str,
    *,
    allowed_hosts: Collection[str] | None = None,
    resolve_dns: bool = True,
) -> bool:
    """Validate one redirect hop without ever broadening its trust boundary."""

    try:
        origin = validate_official_url(original_url, resolve_dns=resolve_dns)
        origin_host = normalize_host(urlsplit(origin).hostname or "")
        validate_official_url(
            target_url,
            allowed_hosts=allowed_hosts or {origin_host},
            resolve_dns=resolve_dns,
        )
    except OfficialUrlError:
        return False
    return True
