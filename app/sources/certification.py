from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from app.sources.base import SourceContractError

# Las 18 fuentes acordadas bloquean exclusivamente el relanzamiento inicial.
RELAUNCH_SOURCES = (
    "DOF",
    "SNICE",
    "PLATIICA",
    "Diputados",
    "Senado",
    "IMPI",
    "Gob.mx APF",
    "ONU Noticias",
    "USTR",
    "Trade.gov",
    "CIADI",
    "ANAM",
    "TFJA",
    "OMC",
    "Secretariado T-MEC",
    "Banco Mundial",
    "CPI",
    "CIJ",
)

CertificationStatus = Literal["certified", "degraded", "error", "missing"]


@dataclass(frozen=True)
class SourceCertification:
    source: str
    status: CertificationStatus
    items_found: int
    error: str | None = None
    details: dict[str, Any] | None = None


def certification_report(results: list[Any]) -> dict[str, Any]:
    """Normaliza SourceResult u objetos ya serializados para el gate inicial."""

    by_source: dict[str, SourceCertification] = {}
    for result in results:
        raw = result if isinstance(result, dict) else _result_mapping(result)
        source = str(raw.get("source") or "")
        if not source:
            continue
        status = _normalize_status(str(raw.get("status") or ""), raw)
        details = dict(raw.get("details") or {}) if isinstance(raw.get("details"), dict) else {}
        for key in ("attempts", "validated_endpoints", "warnings"):
            if key in raw:
                details[key] = raw[key]
        by_source[source] = SourceCertification(
            source=source,
            status=status,
            items_found=int(raw.get("items_found") or 0),
            error=str(raw["error"]) if raw.get("error") else None,
            details=details or None,
        )

    sources = [
        by_source.get(
            source,
            SourceCertification(source=source, status="missing", items_found=0),
        )
        for source in RELAUNCH_SOURCES
    ]
    counts = {
        status: sum(item.status == status for item in sources)
        for status in ("certified", "degraded", "error", "missing")
    }
    return {
        "required_sources": len(RELAUNCH_SOURCES),
        "relaunch_ready": counts == {
            "certified": len(RELAUNCH_SOURCES),
            "degraded": 0,
            "error": 0,
            "missing": 0,
        },
        "counts": counts,
        "sources": [asdict(item) for item in sources],
    }


def assert_relaunch_ready(report: dict[str, Any]) -> None:
    if report.get("relaunch_ready") is True:
        return
    failures = [
        f"{item.get('source')}: {item.get('status')}"
        for item in report.get("sources", [])
        if item.get("status") != "certified"
    ]
    raise SourceContractError(
        "Relanzamiento bloqueado; fuentes no certificadas: " + ", ".join(failures)
    )


def _result_mapping(result: Any) -> dict[str, Any]:
    status_dict = getattr(result, "status_dict", None)
    if callable(status_dict):
        raw = dict(status_dict())
        details = getattr(result, "details", None)
        if isinstance(details, dict):
            raw["details"] = details
        return raw
    return {
        "source": getattr(result, "source", ""),
        "status": getattr(result, "status", ""),
        "items_found": len(getattr(result, "candidates", []) or []),
        "error": getattr(result, "error", None),
        "degraded": getattr(result, "degraded", False),
        "details": getattr(result, "details", None),
    }


def _normalize_status(raw_status: str, raw: dict[str, Any]) -> CertificationStatus:
    status = raw_status.casefold()
    details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
    warnings = raw.get("warnings", details.get("warnings"))
    if raw.get("error") or status == "error":
        return "error"
    if raw.get("degraded") or status == "degraded" or warnings:
        return "degraded"
    if status in {"ok", "certified"}:
        validated = raw.get("validated_endpoints", details.get("validated_endpoints"))
        if type(validated) is int and validated >= 1:
            return "certified"
        return "degraded"
    return "missing"
