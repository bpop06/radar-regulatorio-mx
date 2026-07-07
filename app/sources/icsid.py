from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import Candidate
from app.sources.base import Collector
from app.text import clean_text, normalized

# Ruta por defecto del snapshot local (relativa al directorio de trabajo del
# proceso, igual que `database_path` en app/config.py). Configurable por
# variable de entorno o por parámetro del constructor/parse — ver docstring
# de IcsidCollector.
DEFAULT_SNAPSHOT_PATH = Path("data/icsid_snapshot.json")

STATUS_LABELS = {
    "Pending": "Pendiente",
    "Concluded": "Concluido",
}


class IcsidCollector(Collector):
    """Casos del CIADI (ICSID) donde México es parte, vía su API pública.

    El endpoint `https://icsid.worldbank.org/api/all/cases` no expone campos
    de "materia"/"sector económico" (`subject`/`econsector` llegan vacíos
    para todos los casos, no solo los de México) ni una fecha de
    actualización del caso: solo trae partes, número de caso y estatus
    ("Pending"/"Concluded"). Por eso la estrategia de novedad no puede ser
    por fecha como en el resto de los recolectores:

    - Se guarda un snapshot local `{caseno: status}` (por defecto en
      `data/icsid_snapshot.json`, fuera de git — ver .gitignore); la ruta es
      configurable con la variable de entorno `RADAR_ICSID_SNAPSHOT` o con el
      parámetro `snapshot_path` del constructor/`parse`, para poder aislarla
      en pruebas con `tmp_path`.
    - En cada corrida se compara el estatus de cada caso mexicano contra el
      snapshot anterior. Solo se emite un Candidate para los casos NUEVOS
      (número de caso ausente en el snapshot previo) o con CAMBIO de estatus
      desde la corrida anterior. El resto —casos ya vistos y sin cambios— no
      generan ruido diario.
    - Sin snapshot previo (primera corrida en un entorno nuevo) hay ~56 casos
      históricos de México; emitir todos inundaría el radar con arbitrajes ya
      resueltos hace años. Por eso, en la primera corrida solo se emiten los
      casos con estatus "Pending" (los que siguen activos), y se guarda el
      snapshot completo (Pending + Concluded) como línea base para detectar
      cambios futuros en cualquier caso, incluidos los ya concluidos.
    - Como la API no da fecha del caso, `published_at` de los candidatos
      emitidos es la fecha de hoy en la zona horaria configurada
      (`LOCAL_TIMEZONE`, igual que el resto del pipeline): representa cuándo
      el radar detectó la novedad, no cuándo ocurrió en el CIADI.
    """

    source = "CIADI"
    url = "https://icsid.worldbank.org/api/all/cases"
    authority = "Centro Internacional de Arreglo de Diferencias Relativas a Inversiones"
    document_type = "Caso de arbitraje de inversión"

    def __init__(
        self,
        client,
        snapshot_path: str | Path | None = None,
        persist_snapshot: bool = True,
    ) -> None:
        super().__init__(client)
        self.snapshot_path = Path(snapshot_path) if snapshot_path else _default_snapshot_path()
        # En corridas de prueba (`collect --dry-run`) el snapshot NO debe
        # persistirse: si se guardara, la novedad quedaría "consumida" sin
        # haberse publicado nunca y la siguiente corrida real la omitiría.
        self.persist_snapshot = persist_snapshot

    async def collect(self, since: date) -> list[Candidate]:
        # `since` no se usa: la novedad de esta fuente se decide por cambio
        # de estatus contra el snapshot local, no por fecha (ver docstring).
        response = await self.client.get(self.url)
        response.raise_for_status()
        return self.parse(
            response.json(),
            snapshot_path=self.snapshot_path,
            persist_snapshot=self.persist_snapshot,
        )

    @classmethod
    def parse(
        cls,
        payload: dict[str, Any],
        snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
        today: date | None = None,
        persist_snapshot: bool = True,
    ) -> list[Candidate]:
        snapshot_path = Path(snapshot_path)
        published_at = today or _local_today()

        cases = _mexico_cases(payload)
        previous_snapshot = _load_snapshot(snapshot_path)
        is_first_run = previous_snapshot is None
        previous = previous_snapshot or {}

        candidates: list[Candidate] = []
        current_snapshot: dict[str, str] = {}
        for case in cases:
            caseno = clean_text(str(case.get("caseno") or ""))
            if not caseno:
                continue
            status = clean_text(str(case.get("status") or ""))
            current_snapshot[caseno] = status

            if is_first_run:
                if status == "Pending":
                    candidates.append(_build_candidate(case, caseno, status, published_at))
                continue

            previous_status = previous.get(caseno)
            if previous_status is None or previous_status != status:
                candidates.append(_build_candidate(case, caseno, status, published_at))

        if persist_snapshot:
            _save_snapshot(snapshot_path, current_snapshot)
        return candidates


def _default_snapshot_path() -> Path:
    return Path(os.getenv("RADAR_ICSID_SNAPSHOT", str(DEFAULT_SNAPSHOT_PATH)))


def _mexico_cases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    cases = data.get("GetAllCasesResult") if isinstance(data, dict) else None
    if not isinstance(cases, list):
        return []
    return [case for case in cases if isinstance(case, dict) and _is_mexico_case(case)]


def _is_mexico_case(case: dict[str, Any]) -> bool:
    claimant = normalized(str(case.get("claimant") or ""))
    respondent = normalized(str(case.get("respondent") or ""))
    # Cubre "Mexico", "México" y variantes como "United Mexican States" /
    # "Mexican", todas con el radical normalizado "mexic".
    return "mexic" in claimant or "mexic" in respondent


def _build_candidate(
    case: dict[str, Any], caseno: str, status: str, published_at: date
) -> Candidate:
    claimant = clean_text(str(case.get("claimant") or "")) or "Parte no identificada"
    respondent = clean_text(str(case.get("respondent") or "")) or "Parte no identificada"
    status_label = STATUS_LABELS.get(status, status or "Estatus no informado")
    subject = clean_text(str(case.get("subject") or ""))
    econsector = clean_text(str(case.get("econsector") or ""))

    description = f"Estatus del procedimiento: {status_label}."
    if subject:
        description += f" Materia: {subject}."
    if econsector:
        description += f" Sector económico: {econsector}."

    return Candidate(
        source=IcsidCollector.source,
        source_id=hashlib.sha256(caseno.encode()).hexdigest()[:16],
        url=f"https://icsid.worldbank.org/cases/case-database/case-detail?CaseNo={caseno}",
        official_title=f"{claimant} v. {respondent} (Caso CIADI No. {caseno})",
        description=description,
        published_at=published_at,
        authority=IcsidCollector.authority,
        document_type=IcsidCollector.document_type,
        case_parties=f"{claimant} v. {respondent}",
        case_status=status,
    )


def _load_snapshot(path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    return {str(key): str(value) for key, value in raw.items()}


def _save_snapshot(path: Path, snapshot: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _local_today() -> date:
    tz_name = os.getenv("LOCAL_TIMEZONE", "America/Mexico_City")
    try:
        return datetime.now(ZoneInfo(tz_name)).date()
    except ZoneInfoNotFoundError:
        return date.today()
