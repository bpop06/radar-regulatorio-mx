from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bs4 import BeautifulSoup

from app.models import Candidate
from app.sources.base import (
    Collector,
    SnapshotProposal,
    SourceContractError,
    require_mapping_list,
    snapshot_inventory_collapsed,
)
from app.state import load_committed_state
from app.text import clean_text, normalized

DEFAULT_SNAPSHOT_PATH = Path("docs/data/state/icsid.json")

STATUS_LABELS = {
    "Pending": "Pendiente",
    "Concluded": "Concluido",
}


@dataclass(frozen=True)
class IcsidCaseDetail:
    title: str
    subject: str
    economic_sector: str
    treaty: str
    registered_at: date | None
    status: str
    latest_development: str
    latest_development_at: date | None

    @property
    def content_hash(self) -> str:
        material = "\n".join(
            (
                self.title,
                self.subject,
                self.economic_sector,
                self.treaty,
                self.registered_at.isoformat() if self.registered_at else "",
                self.status,
                self.latest_development,
            )
        )
        return hashlib.sha256(material.encode()).hexdigest()


class IcsidCollector(Collector):
    """Casos CIADI de México, con evidencia de cada ficha oficial.

    La lista pública permite descubrir el universo. Las fichas oficiales se
    consultan para todos los asuntos pendientes y para cualquier caso nuevo o
    con cambio de estatus. El snapshot se propone en ``pending_state``; este
    módulo nunca lo escribe. Así, un dry-run o un fallo de validación no puede
    consumir una novedad.
    """

    source = "CIADI"
    url = "https://icsid.worldbank.org/api/all/cases"
    detail_base = "https://icsid.worldbank.org/cases/case-database/case-detail"
    authority = "Centro Internacional de Arreglo de Diferencias Relativas a Inversiones"
    document_type = "Caso de arbitraje de inversión"

    def __init__(
        self,
        client,
        snapshot_path: str | Path | None = None,
        persist_snapshot: bool = False,
        force_bootstrap: bool = False,
    ) -> None:
        super().__init__(client)
        self.snapshot_path = Path(snapshot_path) if snapshot_path else _default_snapshot_path()
        # Se conserva el parámetro para no romper integraciones antiguas, pero
        # la persistencia dentro del recolector está prohibida en schema v8.
        self.persist_snapshot = False
        self.force_bootstrap = force_bootstrap

    async def collect(self, since: date) -> list[Candidate]:
        del since  # La detección es por identidad/huella, no por ventana móvil.
        response = await self.client.get(self.url)
        self.validate_response(response, content_types={"application/json"})
        payload = response.json()
        cases = _mexico_cases(payload)
        previous_snapshot = None if self.force_bootstrap else _load_snapshot(self.snapshot_path)
        bootstrap = previous_snapshot is None
        previous = previous_snapshot or {}

        candidates: list[Candidate] = []
        historical_candidates: list[Candidate] = []
        current: dict[str, str] = {}
        semaphore = asyncio.Semaphore(6)
        bootstrap_incomplete = False

        async def inspect(
            case: dict[str, Any],
        ) -> tuple[str, str, Candidate | None, Candidate | None, bool]:
            caseno = clean_text(str(case.get("caseno") or ""))
            status = clean_text(str(case.get("status") or ""))
            old_raw = previous.get(caseno)
            old_status, old_hash = _decode_state(old_raw)
            must_fetch = status == "Pending" or old_raw is None or old_status != status

            detail: IcsidCaseDetail | None = None
            if must_fetch:
                try:
                    async with semaphore:
                        detail_response = await self.client.get(_case_url(caseno))
                    self.validate_response(detail_response, content_types={"text/html"})
                    detail = parse_case_detail(detail_response.text)
                except Exception as exc:
                    self.mark_degraded(
                        f"{caseno}: no se pudo validar la ficha oficial "
                        f"({type(exc).__name__}: {exc})"
                    )
                    # No avanzar el estado del caso: la siguiente corrida debe
                    # reintentar la evidencia y la novedad.
                    if old_raw is not None:
                        return caseno, old_raw, None, None, bootstrap
                    return caseno, "", None, None, bootstrap

            content_hash = detail.content_hash if detail else old_hash
            state_value = _encode_state(status, content_hash)
            changed = old_raw is None or old_status != status
            if old_hash and content_hash:
                changed = changed or old_hash != content_hash

            # Un snapshot ausente es bootstrap, nunca una edición con todo el
            # inventario histórico (ni siquiera los Pending).
            candidate = None
            historical_candidate = None
            if not bootstrap and changed:
                candidate = _build_candidate(case, caseno, status, _local_today(), detail)
            elif bootstrap:
                historical_candidate = _build_candidate(
                    case, caseno, status, _local_today(), detail
                )
            return caseno, state_value, candidate, historical_candidate, False

        inspected = await asyncio.gather(*(inspect(case) for case in cases))
        for caseno, state_value, candidate, historical_candidate, failed_bootstrap in inspected:
            if state_value:
                current[caseno] = state_value
            if candidate is not None:
                candidates.append(candidate)
            if historical_candidate is not None:
                historical_candidates.append(historical_candidate)
            bootstrap_incomplete = bootstrap_incomplete or failed_bootstrap

        if snapshot_inventory_collapsed(previous_snapshot, current):
            before = len(previous_snapshot or {})
            self.mark_degraded(
                "inventario CIADI vacío o truncado; "
                f"se observaron {len(current)} casos frente a {before} previos"
            )
            self.pending_state = None
            self.historical_candidates = []
            return []

        # Un bootstrap parcial no puede confirmar un snapshot que convierta
        # los casos pendientes de evidencia en "nuevos" en la siguiente
        # corrida. Las fichas que sí se validaron pueden publicarse; el estado
        # completo se vuelve a intentar sin consumo prematuro.
        self.pending_state = None if bootstrap_incomplete else current
        self.historical_candidates = historical_candidates
        return candidates

    @classmethod
    def propose(
        cls,
        payload: dict[str, Any],
        *,
        previous: dict[str, str] | None,
        today: date | None = None,
    ) -> SnapshotProposal:
        """Diff determinista sin red, usado por fixtures y migraciones."""

        cases = _mexico_cases(payload)
        bootstrap = previous is None
        seen = previous or {}
        published_at = today or _local_today()
        current: dict[str, str] = {}
        candidates: list[Candidate] = []
        historical_candidates: list[Candidate] = []
        for case in cases:
            caseno = clean_text(str(case.get("caseno") or ""))
            if not caseno:
                continue
            status = clean_text(str(case.get("status") or ""))
            current[caseno] = _encode_state(status, "")
            previous_status, _previous_hash = _decode_state(seen.get(caseno))
            if not bootstrap and (caseno not in seen or previous_status != status):
                candidates.append(_build_candidate(case, caseno, status, published_at, None))
            elif bootstrap:
                historical_candidates.append(
                    _build_candidate(case, caseno, status, published_at, None)
                )
        return SnapshotProposal(
            tuple(candidates),
            current,
            bootstrap,
            tuple(historical_candidates),
        )

    @classmethod
    def parse(
        cls,
        payload: dict[str, Any],
        snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
        today: date | None = None,
        persist_snapshot: bool = False,
    ) -> list[Candidate]:
        del persist_snapshot
        proposal = cls.propose(
            payload,
            previous=_load_snapshot(Path(snapshot_path)),
            today=today,
        )
        return list(proposal.candidates)


def parse_case_detail(payload: str) -> IcsidCaseDetail:
    soup = BeautifulSoup(payload, "html.parser")
    container = soup.select_one(".case__detail")
    if container is None:
        raise SourceContractError("CIADI: la ficha no contiene .case__detail")
    heading = container.find("h1")
    title = clean_text(heading.get_text(" ", strip=True) if heading else "")
    if "ICSID Case No." not in title:
        raise SourceContractError("CIADI: la ficha no contiene el número oficial del caso")

    values: dict[str, str] = {}
    for row in container.select("li.row"):
        label = row.find("label")
        value = row.select_one(".rightcol")
        if label is None or value is None:
            continue
        key = clean_text(label.get_text(" ", strip=True)).rstrip(":")
        # Claimant/Respondent aparecen de nuevo como representantes; los
        # campos sustantivos que usamos son únicos salvo ese par.
        values.setdefault(key, clean_text(value.get_text(" ", strip=True)))

    latest = values.get("Latest Development", "")
    return IcsidCaseDetail(
        title=title,
        subject=values.get("Subject of Dispute", ""),
        economic_sector=values.get("Economic Sector", ""),
        treaty=values.get("Instrument(s) Invoked", ""),
        registered_at=_parse_english_date(values.get("Date Registered", "")),
        status=values.get("Status of Proceeding", ""),
        latest_development=latest,
        latest_development_at=_parse_leading_english_date(latest),
    )


def _default_snapshot_path() -> Path:
    return Path(os.getenv("RADAR_ICSID_SNAPSHOT", str(DEFAULT_SNAPSHOT_PATH)))


def _mexico_cases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_cases = require_mapping_list(payload, "data", "GetAllCasesResult", source="CIADI")
    return [case for case in raw_cases if isinstance(case, dict) and _is_mexico_case(case)]


def _is_mexico_case(case: dict[str, Any]) -> bool:
    claimant = normalized(str(case.get("claimant") or ""))
    respondent = normalized(str(case.get("respondent") or ""))
    return "mexic" in claimant or "mexic" in respondent


def _build_candidate(
    case: dict[str, Any],
    caseno: str,
    status: str,
    detected_on: date,
    detail: IcsidCaseDetail | None,
) -> Candidate:
    claimant = clean_text(str(case.get("claimant") or "")) or "Parte no identificada"
    respondent = clean_text(str(case.get("respondent") or "")) or "Parte no identificada"
    status_label = STATUS_LABELS.get(status, status or "Estatus no informado")
    subject = detail.subject if detail else clean_text(str(case.get("subject") or ""))
    econsector = (
        detail.economic_sector if detail else clean_text(str(case.get("econsector") or ""))
    )
    treaty = detail.treaty if detail else ""
    latest = detail.latest_development if detail else ""

    description_parts = [f"Estatus del procedimiento: {status_label}."]
    if subject:
        description_parts.append(f"Materia: {subject}.")
    if econsector:
        description_parts.append(f"Sector económico: {econsector}.")
    if treaty:
        description_parts.append(f"Instrumento invocado: {treaty}.")
    if latest:
        description_parts.append(f"Última actuación: {latest}")

    official_date = None
    if detail:
        official_date = detail.latest_development_at or detail.registered_at
    if official_date is None:
        official_date = _registered_date_from_api(case)
    published_at = official_date or detected_on
    url = _case_url(caseno)
    official_title = (
        detail.title
        if detail
        else f"{claimant} v. {respondent} (Caso CIADI No. {caseno})"
    )
    evidence = {
        "case_detail": url,
        "status": detail.status if detail and detail.status else status,
    }
    if subject:
        evidence["subject"] = subject
    if treaty:
        evidence["instrument_invoked"] = treaty
    if latest:
        evidence["latest_development"] = latest

    return Candidate(
        source=IcsidCollector.source,
        source_id=caseno,
        url=url,
        canonical_url=url,
        official_title=official_title,
        description=" ".join(description_parts),
        published_at=published_at,
        official_published_at=official_date,
        detected_at=datetime.now(UTC),
        authority=IcsidCollector.authority,
        document_type=IcsidCollector.document_type,
        case_number=caseno,
        case_parties=f"{claimant} v. {respondent}",
        case_status=detail.status if detail and detail.status else status,
        case_treaty=treaty,
        case_claim=subject,
        official_evidence=evidence,
    )


def _registered_date_from_api(case: dict[str, Any]) -> date | None:
    proceedings = case.get("caseproceedings")
    if not isinstance(proceedings, list):
        return None
    for proceeding in proceedings:
        if not isinstance(proceeding, dict):
            continue
        raw = clean_text(str(proceeding.get("dateregistered") or ""))
        for pattern in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y"):
            try:
                return datetime.strptime(raw, pattern).date()
            except ValueError:
                continue
    return None


def _load_snapshot(path: Path) -> dict[str, str] | None:
    raw = load_committed_state(path, source="icsid")
    if raw is None:
        return None
    return {str(key): str(value) for key, value in raw.items()}


def _encode_state(status: str, content_hash: str) -> str:
    return json.dumps(
        {"content_hash": content_hash, "status": status},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode_state(raw: str | None) -> tuple[str, str]:
    if raw is None:
        return "", ""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return raw, ""  # snapshot v7: {caseno: status}
    if not isinstance(value, dict):
        return raw, ""
    return clean_text(str(value.get("status") or "")), clean_text(
        str(value.get("content_hash") or "")
    )


def _case_url(caseno: str) -> str:
    return f"{IcsidCollector.detail_base}?{urlencode({'CaseNo': caseno})}"


def _parse_english_date(raw: str) -> date | None:
    for pattern in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(clean_text(raw), pattern).date()
        except ValueError:
            continue
    return None


def _parse_leading_english_date(raw: str) -> date | None:
    match = re.match(r"^([A-Za-z]+\s+\d{1,2},\s+\d{4})\b", clean_text(raw))
    return _parse_english_date(match.group(1)) if match else None


def _local_today() -> date:
    tz_name = os.getenv("LOCAL_TIMEZONE", "America/Mexico_City")
    try:
        return datetime.now(ZoneInfo(tz_name)).date()
    except ZoneInfoNotFoundError:
        return date.today()
