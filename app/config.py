from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    request_timeout: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
    lookback_days: int = int(os.getenv("LOOKBACK_DAYS", "31"))
    minimum_relevance: int = int(os.getenv("MINIMUM_RELEVANCE", "2"))
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY") or None
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.5")
    user_agent: str = os.getenv(
        "USER_AGENT",
        "RadarRegulatorioMX/0.1 (+https://github.com/; contacto: administrador-del-sitio)",
    )

