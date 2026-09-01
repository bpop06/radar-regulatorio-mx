from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _default_database_path() -> str:
    state_dir = os.getenv("RADAR_STATE_DIR")
    if state_dir:
        return str(Path(state_dir).expanduser() / "radar.sqlite3")
    return str(
        Path.home()
        / "Library/Application Support/Radar Regulatorio MX/radar.sqlite3"
    )


@dataclass(frozen=True)
class Settings:
    request_timeout: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
    lookback_days: int = int(os.getenv("LOOKBACK_DAYS", "31"))
    minimum_relevance: int = int(os.getenv("MINIMUM_RELEVANCE", "2"))
    source_retries: int = int(os.getenv("SOURCE_RETRIES", "3"))
    source_retry_backoff_seconds: float = float(
        os.getenv("SOURCE_RETRY_BACKOFF_SECONDS", "1.25")
    )
    source_deadline_seconds: float = float(
        os.getenv("SOURCE_DEADLINE_SECONDS", "120")
    )
    collection_deadline_seconds: float = float(
        os.getenv("COLLECTION_DEADLINE_SECONDS", "300")
    )
    state_max_bytes: int = int(os.getenv("RADAR_STATE_MAX_BYTES", str(512 * 1024 * 1024)))
    local_timezone: str = os.getenv("LOCAL_TIMEZONE", "America/Mexico_City")
    database_path: str = os.getenv("RADAR_DB_PATH", _default_database_path())
    user_agent: str = os.getenv(
        "USER_AGENT",
        "RadarRegulatorioMX/0.1 (+https://github.com/; contacto: administrador-del-sitio)",
    )
