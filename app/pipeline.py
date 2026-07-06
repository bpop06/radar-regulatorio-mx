from __future__ import annotations

import asyncio
import json
import ssl
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import truststore

from app.config import Settings
from app.markdown import build_detail_markdown, detail_url
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
    lookback = days if days is not None else settings.lookback_days
    since = _local_today(settings.local_timezone) - timedelta(days=lookback)
    headers = {"User-Agent": settings.user_agent}
    timeout = httpx.Timeout(settings.request_timeout)
    ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    source_retries = max(1, settings.source_retries)
    retry_backoff = max(0.0, settings.source_retry_backoff_seconds)

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
            *(
                _collect_source(collector, since, source_retries, retry_backoff)
                for collector in collectors
            )
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
        published_at = candidate.published_at.isoformat()
        full_markdown = build_detail_markdown(
            title=summary.title,
            summary=summary.summary,
            official_title=candidate.official_title,
            description=candidate.description,
            source=candidate.source,
            authority=candidate.authority,
            document_type=candidate.document_type,
            published_at=published_at,
            categories=item.categories,
            source_url=candidate.url,
            ai_generated=summary.ai_generated,
        )
        publications.append(
            Publication(
                id=candidate.id,
                source=candidate.source,
                url=candidate.url,
                detail_url=detail_url(candidate.id),
                official_title=candidate.official_title,
                title=summary.title,
                summary=summary.summary,
                description=candidate.description,
                detail_markdown=full_markdown,
                published_at=published_at,
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


async def _collect_source(
    collector,
    since: date,
    attempts: int,
    backoff_seconds: float,
) -> SourceResult:
    last_error: Exception | None = None
    for attempt_index in range(attempts):
        current_attempt = attempt_index + 1
        try:
            candidates = await collector.collect(since)
            return SourceResult(
                source=collector.source,
                candidates=candidates,
                attempts=current_attempt,
            )
        except Exception as exc:
            last_error = exc
            if current_attempt < attempts:
                await asyncio.sleep(backoff_seconds * current_attempt)

    if last_error is None:
        error = "UnknownError: source collection failed"
    else:
        error = f"{type(last_error).__name__}: {str(last_error)[:240]}"
    return SourceResult(source=collector.source, error=error, attempts=attempts)


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


def _local_today(timezone_name: str) -> date:
    try:
        return datetime.now(ZoneInfo(timezone_name)).date()
    except ZoneInfoNotFoundError:
        return date.today()
