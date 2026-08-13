from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

import httpx

from app.models import Candidate


class SourceContractError(RuntimeError):
    """La respuesta llegó, pero no satisface el contrato de la fuente.

    Es deliberadamente distinta de un cero válido. El pipeline puede publicar
    las demás fuentes, pero nunca debe presentar esta fuente como ``ok: 0``.
    """


@dataclass
class CollectorDiagnostics:
    validated_endpoints: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def status(self) -> Literal["certified", "degraded"]:
        return "degraded" if self.warnings else "certified"


@dataclass(frozen=True)
class SnapshotProposal:
    """Estado que se escribe únicamente al publicar el corte completo."""

    candidates: tuple[Candidate, ...]
    state: dict[str, str]
    bootstrap: bool = False
    historical_candidates: tuple[Candidate, ...] = ()


SNAPSHOT_MIN_RETENTION_RATIO = 0.75


def snapshot_inventory_collapsed(
    previous: dict[str, str] | None,
    current: dict[str, str],
) -> bool:
    """Detecta vacíos y truncamientos incompatibles con un snapshot sano."""

    if not current:
        return True
    if not previous:
        return False
    return len(current) / len(previous) < SNAPSHOT_MIN_RETENTION_RATIO


class Collector(ABC):
    source: str

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.diagnostics = CollectorDiagnostics()
        self.pending_state: dict[str, str] | None = None
        # Inventarios descubiertos durante un bootstrap viajan por un canal
        # separado. El pipeline puede crear/actualizar fichas permanentes sin
        # presentarlos como novedades del corte vigente.
        self.historical_candidates: list[Candidate] = []

    @abstractmethod
    async def collect(self, since: date) -> list[Candidate]:
        raise NotImplementedError

    def mark_degraded(self, reason: str) -> None:
        reason = " ".join(reason.split())[:300]
        if reason and reason not in self.diagnostics.warnings:
            self.diagnostics.warnings.append(reason)

    def validate_response(
        self,
        response: httpx.Response,
        *,
        content_types: Collection[str],
        require_body: bool = True,
    ) -> None:
        """Valida transporte y representación antes de aceptar el payload.

        Los ``content_types`` aceptan tipos exactos o prefijos que terminan en
        ``/``. El parámetro existe para fuentes que publican variantes
        registradas del mismo formato (por ejemplo ``application/rss+xml``).
        """

        response.raise_for_status()
        content_type = (
            response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        )
        if not _content_type_matches(content_type, content_types):
            expected = ", ".join(sorted(content_types))
            actual = content_type or "ausente"
            raise SourceContractError(
                f"{self.source}: Content-Type inesperado {actual!r}; se esperaba {expected}"
            )
        if require_body and not response.content.strip():
            raise SourceContractError(f"{self.source}: respuesta vacía")
        self.diagnostics.validated_endpoints += 1


def require_mapping_list(payload: Any, *path: str, source: str) -> list[Any]:
    """Resuelve una lista JSON obligatoria y distingue estructura rota de cero."""

    current = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            joined = ".".join(path)
            raise SourceContractError(f"{source}: falta la lista JSON {joined}")
        current = current[key]
    if not isinstance(current, list):
        joined = ".".join(path)
        raise SourceContractError(f"{source}: {joined} no es una lista")
    return current


def require_html_marker(payload: str, marker: str, *, source: str) -> None:
    if marker.casefold() not in payload.casefold():
        raise SourceContractError(f"{source}: no se encontró la estructura HTML {marker!r}")


def require_xml_channel(payload: bytes, *, source: str) -> None:
    lowered = payload[:100_000].lower()
    if b"<rss" not in lowered or b"<channel" not in lowered:
        raise SourceContractError(f"{source}: la respuesta no contiene un canal RSS")


def _content_type_matches(actual: str, expected: Collection[str]) -> bool:
    return any(
        actual == accepted or (accepted.endswith("/") and actual.startswith(accepted))
        for accepted in expected
    )
