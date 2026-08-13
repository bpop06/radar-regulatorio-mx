from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from app.models import Candidate
from app.sources.base import (
    Collector,
    SnapshotProposal,
    SourceContractError,
    snapshot_inventory_collapsed,
)
from app.sources.icsid import _local_today
from app.state import load_committed_state
from app.text import clean_text, normalized

DEFAULT_SNAPSHOT_PATH = Path("docs/data/state/tmec.json")

# Valores observados en las tablas oficiales de capítulos 10 y 31. Se
# conservan ``Complete`` y ``Completed`` porque la propia fuente usa ambos.
VALID_STATUSES = frozenset(
    {
        "Active",
        "Complete",
        "Completed",
        "Pending",
        "Suspended",
        "Terminated",
        "Withdrawn",
    }
)

HEADER_ALIASES = {
    "number": {"panel review number", "numero de la revision"},
    "title": {"title", "titulo"},
    "instrument": {"instrument invoked", "instrumento invocado"},
    "mechanism": {
        "dispute settlement mechanism",
        "mecanismo de solucion de controversias",
        "mechanism",
    },
    "complaining_party": {"complaining party", "importing party", "parte importador"},
    "responding_party": {
        "responding party",
        "other involved party",
        "otra parte implicada",
    },
    "third_party": {"third party", "tercera parte"},
    "panel_composition": {"panel composition", "composicion del panel"},
    "requested_at": {
        "date of panel request",
        "fecha de solicitud del panel",
        "date",
    },
    "status": {"status", "estatus"},
    "final_report_at": {"date of final report", "fecha del informe final"},
}


@dataclass(frozen=True)
class TmecCase:
    number: str
    title: str
    instrument: str
    mechanism: str
    complaining_party: str
    responding_party: str
    third_party: str
    panel_composition: str
    requested_at: date
    status: str
    final_report_at: str
    source_url: str

    @property
    def content_hash(self) -> str:
        material = "\n".join(
            (
                self.number,
                self.title,
                self.instrument,
                self.mechanism,
                self.complaining_party,
                self.responding_party,
                self.third_party,
                self.panel_composition,
                self.requested_at.isoformat(),
                self.status,
                self.final_report_at,
            )
        )
        return hashlib.sha256(material.encode()).hexdigest()


class TmecCollector(Collector):
    """Paneles T-MEC por encabezados semánticos, no por posición fija."""

    source = "Secretariado T-MEC"
    base = "https://can-mex-usa-sec.org/secretariat"
    urls = (
        f"{base}/dispute-differends-controversias/"
        "chapter-chapitre-capitulo_10.aspx?lang=eng",
        f"{base}/dispute-differends-controversias/"
        "chapter-chapitre-capitulo_31.aspx?lang=eng",
    )
    authority = "Secretariado del T-MEC"
    document_type = "Panel de solución de controversias"

    def __init__(
        self,
        client,
        snapshot_path: str | Path | None = None,
        persist_snapshot: bool = False,
        force_bootstrap: bool = False,
    ) -> None:
        super().__init__(client)
        self.snapshot_path = Path(
            snapshot_path or os.getenv("RADAR_TMEC_SNAPSHOT", str(DEFAULT_SNAPSHOT_PATH))
        )
        self.persist_snapshot = False
        self.force_bootstrap = force_bootstrap

    async def collect(self, since: date) -> list[Candidate]:
        del since
        pages: list[tuple[str, str]] = []
        for url in self.urls:
            try:
                response = await self.client.get(url)
                self.validate_response(response, content_types={"text/html"})
                # Parsear aquí certifica la estructura antes de contar el
                # endpoint como útil; una página parcial no borra la otra.
                parse_tmec_page(response.text, url)
                pages.append((response.text, url))
            except Exception as exc:
                self.mark_degraded(f"{url}: {type(exc).__name__}: {exc}")
        if not pages:
            raise SourceContractError("Secretariado T-MEC: fallaron las dos tablas oficiales")

        previous = None if self.force_bootstrap else _load_snapshot(self.snapshot_path)
        proposal = self.propose(pages, previous=previous, today=_local_today())
        # Si solo una tabla respondió, preservar el estado de la otra evita
        # que su retorno se confunda con un inventario nuevo.
        pending = dict(previous or {}) if len(pages) != len(self.urls) else {}
        pending.update(proposal.state)
        # Si ambas tablas parecen válidas pero perdieron de golpe gran parte
        # del inventario, no consumir el snapshot ni emitir falsos cambios.
        # Con una tabla caída el merge conservador anterior preserva las filas
        # ausentes y el diagnóstico parcial ya marca cobertura degradada.
        if len(pages) == len(self.urls) and snapshot_inventory_collapsed(
            previous, proposal.state
        ):
            before = len(previous or {})
            self.mark_degraded(
                "inventario T-MEC vacío o truncado; "
                f"se observaron {len(proposal.state)} casos frente a {before} previos"
            )
            self.pending_state = None
            self.historical_candidates = []
            return []
        # Igual que CIADI, un primer inventario incompleto no confirma estado:
        # al volver la tabla faltante debe completar el histórico, no aparecer
        # como novedad del día.
        self.pending_state = (
            None if proposal.bootstrap and len(pages) != len(self.urls) else pending
        )
        self.historical_candidates = list(proposal.historical_candidates)
        return list(proposal.candidates)

    @classmethod
    def propose(
        cls,
        pages: list[tuple[str, str]],
        *,
        previous: dict[str, str] | None,
        today: date | None = None,
    ) -> SnapshotProposal:
        detected_on = today or _local_today()
        bootstrap = previous is None
        seen = previous or {}
        current: dict[str, str] = {}
        candidates: list[Candidate] = []
        historical_candidates: list[Candidate] = []

        for payload, source_url in pages:
            for case in parse_tmec_page(payload, source_url):
                state_value = _encode_state(case.status, case.content_hash)
                current[case.number] = state_value
                old_status, old_hash = _decode_state(seen.get(case.number))
                changed = case.number not in seen or old_status != case.status
                if old_hash:
                    changed = changed or old_hash != case.content_hash
                if not bootstrap and changed:
                    candidates.append(_build_candidate(case, detected_on))
                elif bootstrap:
                    historical_candidates.append(_build_candidate(case, detected_on))

        return SnapshotProposal(
            tuple(candidates),
            current,
            bootstrap,
            tuple(historical_candidates),
        )

    @classmethod
    def parse(
        cls,
        pages: list[str] | str,
        snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
        today: date | None = None,
        persist_snapshot: bool = False,
    ) -> list[Candidate]:
        del persist_snapshot
        if isinstance(pages, str):
            pages = [pages]
        paired = list(zip(pages, cls.urls, strict=False))
        proposal = cls.propose(
            paired,
            previous=_load_snapshot(Path(snapshot_path)),
            today=today,
        )
        return list(proposal.candidates)


def parse_tmec_page(payload: str, source_url: str) -> list[TmecCase]:
    soup = BeautifulSoup(payload, "html.parser")
    selected_table = None
    header_map: dict[str, int] = {}
    for table in soup.find_all("table"):
        headers = [clean_text(cell.get_text(" ", strip=True)) for cell in table.find_all("th")]
        candidate_map = _map_headers(headers)
        if {"number", "title", "instrument", "mechanism", "requested_at", "status"}.issubset(
            candidate_map
        ):
            selected_table = table
            header_map = candidate_map
            break
    if selected_table is None:
        raise SourceContractError(
            "Secretariado T-MEC: tabla sin encabezados obligatorios de paneles"
        )

    cases: list[TmecCase] = []
    for row in selected_table.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if not cells:
            continue
        values = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
        number = _field(values, header_map, "number")
        if not re.match(r"^[A-Z]{3}-[A-Z]{3}-\d{4}-", number):
            continue
        status = _field(values, header_map, "status")
        if status not in VALID_STATUSES:
            raise SourceContractError(
                f"Secretariado T-MEC: estatus desconocido {status!r} en {number}"
            )
        raw_request_date = _field(values, header_map, "requested_at")
        try:
            requested_at = date.fromisoformat(raw_request_date)
        except ValueError as exc:
            raise SourceContractError(
                f"Secretariado T-MEC: fecha inválida {raw_request_date!r} en {number}"
            ) from exc

        cases.append(
            TmecCase(
                number=number,
                title=_field(values, header_map, "title"),
                instrument=_field(values, header_map, "instrument"),
                mechanism=_field(values, header_map, "mechanism"),
                complaining_party=_field(values, header_map, "complaining_party"),
                responding_party=_field(values, header_map, "responding_party"),
                third_party=_field(values, header_map, "third_party"),
                panel_composition=_field(values, header_map, "panel_composition"),
                requested_at=requested_at,
                status=status,
                final_report_at=_field(values, header_map, "final_report_at"),
                source_url=_case_url(source_url, number),
            )
        )
    return cases


def _map_headers(headers: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for index, header in enumerate(headers):
        value = normalized(header)
        for canonical, aliases in HEADER_ALIASES.items():
            if value in aliases:
                result[canonical] = index
                break
    return result


def _field(values: list[str], header_map: dict[str, int], name: str) -> str:
    index = header_map.get(name)
    return values[index] if index is not None and index < len(values) else ""


def _case_url(source_url: str, number: str) -> str:
    """Ancla estable del caso dentro del listado oficial.

    El portal no expone fichas ni anchors por fila. Conservamos el URL
    canónico de la tabla con un query identificador no ambiguo, de modo que
    cada caso tenga una URL estable sin inventar una ruta oficial inexistente.
    """
    parts = urlsplit(source_url)
    query = urlencode({"lang": "eng", "case": number})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _build_candidate(case: TmecCase, detected_on: date) -> Candidate:
    del detected_on
    parties = " / ".join(
        part
        for part in (case.complaining_party, case.responding_party, case.third_party)
        if part and part != "N/A"
    )
    description_parts = [
        f"Mecanismo: {case.mechanism}.",
        f"Partes: {parties}.",
        f"Solicitud de panel: {case.requested_at.isoformat()}.",
        f"Estatus: {case.status}.",
    ]
    if case.final_report_at and case.final_report_at != "N/A":
        description_parts.append(f"Informe final: {case.final_report_at}.")
    evidence = {
        "case_listing": case.source_url,
        "instrument_invoked": case.instrument,
        "mechanism": case.mechanism,
        "status": case.status,
    }
    if case.panel_composition:
        evidence["panel_composition"] = case.panel_composition
    if case.final_report_at and case.final_report_at != "N/A":
        evidence["final_report_date"] = case.final_report_at

    return Candidate(
        source=TmecCollector.source,
        source_id=case.number,
        url=case.source_url,
        canonical_url=case.source_url,
        official_title=f"{case.title} (Panel {case.number})",
        description=" ".join(description_parts),
        published_at=case.requested_at,
        official_published_at=case.requested_at,
        detected_at=datetime.now(UTC),
        authority=TmecCollector.authority,
        document_type=TmecCollector.document_type,
        case_number=case.number,
        case_parties=parties,
        case_status=case.status,
        case_treaty=case.instrument,
        case_claim=case.title,
        official_evidence=evidence,
    )


def _load_snapshot(path: Path) -> dict[str, str] | None:
    raw = load_committed_state(path, source="tmec")
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
        return raw, ""
    if not isinstance(value, dict):
        return raw, ""
    return clean_text(str(value.get("status") or "")), clean_text(
        str(value.get("content_hash") or "")
    )
