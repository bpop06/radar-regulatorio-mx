from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    request_timeout: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
    lookback_days: int = int(os.getenv("LOOKBACK_DAYS", "31"))
    minimum_relevance: int = int(os.getenv("MINIMUM_RELEVANCE", "2"))
    source_retries: int = int(os.getenv("SOURCE_RETRIES", "3"))
    source_retry_backoff_seconds: float = float(
        os.getenv("SOURCE_RETRY_BACKOFF_SECONDS", "1.25")
    )
    local_timezone: str = os.getenv("LOCAL_TIMEZONE", "America/Mexico_City")
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY") or None
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.5")
    openai_reasoning_effort: str = os.getenv("OPENAI_REASONING_EFFORT", "extra_high")
    database_path: str = os.getenv("RADAR_DB_PATH", "data/radar.sqlite3")
    user_agent: str = os.getenv(
        "USER_AGENT",
        "RadarRegulatorioMX/0.1 (+https://github.com/; contacto: administrador-del-sitio)",
    )
