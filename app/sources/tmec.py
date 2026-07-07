from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date
from html import unescape
from pathlib import Path

from app.models import Candidate
from app.sources.base import Collector
from app.sources.icsid import _local_today
from app.text import clean_text

# Snapshot {panel: estatus} COMMITEADO (igual que el del CIADI): la rutina
# corre en sesiones efímeras y el estado debe sobrevivir entre corridas.
DEFAULT_SNAPSHOT_PATH = Path("docs/data/tmec_snapshot.json")

ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)


class TmecCollector(Collector):
    """Paneles de solución de controversias del Secretariado del T-MEC.

    Verificado en vivo desde este entorno: las páginas .aspx de capítulo 10
    (remedios comerciales) y capítulo 31 (Estado-Estado) son HTML renderizado
    en servidor, público y sin login, con una tabla por página: número de
    panel, título, instrumento, mecanismo, parte importadora, otra parte,
    fecha de solicitud y estatus. robots.txt devuelve 404 (sin restricción).

    Como en el CIADI, la novedad es por snapshot-diff sobre el estatus del
    panel: primera corrida emite todos los paneles listados (el universo es
    pequeño); después, solo paneles nuevos o con cambio de estatus.
    """

    source = "Secretariado T-MEC"
    base = "https://can-mex-usa-sec.org/secretariat"
    urls = (
        f"{base}/dispute-differends-controversias/chapter-chapitre-capitulo_10.aspx",
        f"{base}/dispute-differends-controversias/chapter-chapitre-capitulo_31.aspx",
    )
    authority = "Secretariado del T-MEC"
    document_type = "Panel de solución de controversias"

    def __init__(
        self,
        client,
        snapshot_path: str | Path | None = None,
        persist_snapshot: bool = True,
    ) -> None:
        super().__init__(client)
        self.snapshot_path = Path(
            snapshot_path or os.getenv("RADAR_TMEC_SNAPSHOT", str(DEFAULT_SNAPSHOT_PATH))
        )
        self.persist_snapshot = persist_snapshot

    async def collect(self, since: date) -> list[Candidate]:
        pages: list[str] = []
        for url in self.urls:
            response = await self.client.get(url)
            response.raise_for_status()
            pages.append(response.text)
        return self.parse(
            pages,
            snapshot_path=self.snapshot_path,
            persist_snapshot=self.persist_snapshot,
        )

    @classmethod
    def parse(
        cls,
        pages: list[str] | str,
        snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
        today: date | None = None,
        persist_snapshot: bool = True,
    ) -> list[Candidate]:
        if isinstance(pages, str):
            pages = [pages]
        snapshot_path = Path(snapshot_path)
        published_at = today or _local_today()

        previous: dict[str, str] | None = None
        if snapshot_path.exists():
            try:
                raw = json.loads(snapshot_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    previous = {str(k): str(v) for k, v in raw.items()}
            except (json.JSONDecodeError, OSError):
                previous = None
        is_first_run = previous is None
        seen = previous or {}

        candidates: list[Candidate] = []
        current: dict[str, str] = {}
        for page, source_url in zip(pages, cls.urls):
            for row in ROW_RE.findall(page):
                cells = [
                    clean_text(re.sub(r"<[^>]+>", " ", unescape(cell)))
                    for cell in CELL_RE.findall(row)
                ]
                if len(cells) < 8 or not re.match(r"^[A-Z]{2,4}-", cells[0]):
                    continue  # encabezados o filas ajenas a la tabla de paneles
                number, title, _instr, mechanism, importing, other, requested, status = cells[:8]
                current[number] = status
                is_new = number not in seen
                changed = not is_new and seen[number] != status
                if not (is_first_run or is_new or changed):
                    continue
                candidates.append(
                    Candidate(
                        source=cls.source,
                        source_id=hashlib.sha256(number.encode()).hexdigest()[:16],
                        url=source_url,
                        official_title=f"{title} (Panel {number})",
                        description=(
                            f"Panel del T-MEC {number}. Mecanismo: {mechanism}. "
                            f"Parte importadora: {importing}. Otra parte involucrada: {other}. "
                            f"Solicitud de panel: {requested}. Estatus: {status}."
                        ),
                        published_at=published_at,
                        authority=cls.authority,
                        document_type=cls.document_type,
                        case_parties=f"{importing} / {other}",
                        case_status=status,
                    )
                )

        if persist_snapshot and current:
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(
                json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        return candidates
