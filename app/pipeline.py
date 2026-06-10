from __future__ import annotations

import asyncio
import json
import ssl
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import truststore

from app.config import Settings
from app.models import Candidate, Publication, SourceResult
from app.relevance import classify, is_relevant
from app.sources import (
    DiputadosCollector,
    DofCollector,
    GobMxCollector,
    ImpiCollector,
    PlatiicaCollector,
    SenadoCollector,
    SniceCollector,
)
from app.summarizer import Summarizer
from app.text import normalized


async def collect(settings: Settings, days: int | None = None) -> dict[str, object]:
    lookback = days or settings.lookback_days
    since = date.today() - timedelta(days=lookback)
    headers = {"User-Agent": settings.user_agent}
    timeout = httpx.Timeout(settings.request_timeout)
    ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
        verify=ssl_context,
    ) as client:
        collectors = [
            DofCollector(client),
            SniceCollector(client),
            PlatiicaCollector(client),
            DiputadosCollector(client),
            SenadoCollector(client),
            ImpiCollector(client),
            GobMxCollector(client),
        ]
        results = await asyncio.gather(
            *(_collect_source(collector, since) for collector in collectors)
        )

    unique: dict[str, Candidate] = {}
    for result in results:
        for candidate in result.candidates:
            unique[candidate.id] = candidate

    classified = [
        item
        for item in (classify(candidate) for candidate in _deduplicate(unique.values()))
        if is_relevant(item, settings.minimum_relevance)
    ]
    classified.sort(
        key=lambda item: (
            item.candidate.published_at,
            item.relevance_score,
            item.candidate.source,
        ),
        reverse=True,
    )

    summarizer = Summarizer(settings.openai_api_key, settings.openai_model)
    publications: list[Publication] = []
    for item in classified:
        summary = summarizer.summarize(item)
        candidate = item.candidate
        publications.append(
            Publication(
                id=candidate.id,
                source=candidate.source,
                url=candidate.url,
                official_title=candidate.official_title,
                title=summary.title,
                summary=summary.summary,
                published_at=candidate.published_at.isoformat(),
                authority=candidate.authority,
                document_type=candidate.document_type,
                categories=item.categories,
                relevance_score=item.relevance_score,
                ai_generated=summary.ai_generated,
            )
        )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "lookback_days": lookback,
        "total_items": len(publications),
        "sources": [result.status_dict() for result in results],
        "items": [publication.to_dict() for publication in publications],
    }


async def _collect_source(collector, since: date) -> SourceResult:
    last_error = None
    for attempt in range(2):
        try:
            candidates = await collector.collect(since)
            return SourceResult(source=collector.source, candidates=candidates)
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(0.75)
    return SourceResult(
        source=collector.source,
        error=f"{type(last_error).__name__}: {str(last_error)[:240]}",
    )


def write_output(payload: dict[str, object], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _deduplicate(candidates) -> list[Candidate]:
    selected: dict[tuple[date, str], Candidate] = {}
    for candidate in candidates:
        key = (candidate.published_at, normalized(candidate.official_title))
        current = selected.get(key)
        if current is None or len(candidate.description) > len(current.description):
            selected[key] = candidate
    return list(selected.values())
