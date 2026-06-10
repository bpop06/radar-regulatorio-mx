from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import httpx

from app.models import Candidate


class Collector(ABC):
    source: str

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    @abstractmethod
    async def collect(self, since: date) -> list[Candidate]:
        raise NotImplementedError

