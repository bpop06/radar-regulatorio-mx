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
    AnamCollector,
    CijCollector,
    CpiCollector,
    DiputadosCollector,
    DofCollector,
    GobMxCollector,
    IcsidCollector,
    ImpiCollector,
    OmcCollector,
    OnuNoticiasCollector,
    PlatiicaCollector,
    SenadoCollector,
    SniceCollector,
    TfjaCollector,
    TmecCollector,
    TradeGovCollector,
    UstrCollector,
    WorldBankCollector,
)
from app.summarizer import Summarizer
from app.taxonomy import enrich
from app.text import normalized


async def collect(
    settings: Settings, days: int | None = None, dry_run: bool = False
) -> dict[str, object]:
    lookback = days if days is not None else settings.lookback_days
    since = _local_today(settings.local_timezone) - timedelta(days=lookback)
    headers = {"User-Agent": settings.user_agent}
    timeout = httpx.Timeout(settings.request_timeout)
    ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # Algunos sitios de gobierno mexicano (DOF, Senado) sirven solo el
    # certificado hoja y omiten el intermedio; en Linux el almacén del
    # sistema no lo completa automáticamente y la verificación TLS falla con
    # "unable to get local issuer certificate". Se agregan los intermedios
    # públicos verificados en app/certs/intermediates.pem (ver ese archivo
    # para el detalle de cada cadena) sin reemplazar la confianza del
    # sistema que ya aporta truststore.
    intermediates_path = Path(__file__).parent / "certs" / "intermediates.pem"
    if intermediates_path.exists():
        ssl_context.load_verify_locations(cafile=str(intermediates_path))
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
            OnuNoticiasCollector(client),
            UstrCollector(client),
            TradeGovCollector(client),
            # En dry-run no se persiste el snapshot del CIADI: de lo
            # contrario la novedad se consumiría sin publicarse.
            IcsidCollector(client, persist_snapshot=not dry_run),
            AnamCollector(client),
            TfjaCollector(client),
            OmcCollector(client),
            TmecCollector(client, persist_snapshot=not dry_run),
            WorldBankCollector(client),
            CpiCollector(client),
            CijCollector(client),
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

    summarizer = Summarizer()
    publications: list[Publication] = []
    for item in classified:
        summary = summarizer.summarize(item)
        candidate = item.candidate
        taxonomy = enrich(item)
        published_at = candidate.published_at.isoformat()
        # case_facts no lo produce el pipeline extractivo: lo agrega la
        # editorial (app.editorial) sobre ítems de caso ya publicados y
        # recompone la ficha en ese momento.
        full_markdown = build_detail_markdown(
            title=summary.title,
            summary=summary.summary,
            official_title=candidate.official_title,
            source=candidate.source,
            # Línea meta y fallback muestran el órgano canónico, no la
            # autoridad cruda del recolector (bug #19: "IMSS" o textos en
            # mayúsculas sin resolver contra el catálogo de app.taxonomy).
            authority=taxonomy.issuing_body,
            document_type=candidate.document_type,
            published_at=published_at,
            categories=taxonomy.primary_categories,
            source_url=candidate.url,
            card_body=summary.card_body,
            case_status=candidate.case_status,
            case_parties=candidate.case_parties,
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
                card_body=summary.card_body,
                published_at=published_at,
                authority=candidate.authority,
                document_type=candidate.document_type,
                issuing_body=taxonomy.issuing_body,
                government_branch=taxonomy.government_branch,
                jurisdiction=taxonomy.jurisdiction,
                country_or_org=taxonomy.country_or_org,
                published_year=taxonomy.published_year,
                published_month=taxonomy.published_month,
                published_day=taxonomy.published_day,
                categories=taxonomy.primary_categories,
                topic_tags=taxonomy.topic_tags,
                subtopic_tags=taxonomy.subtopic_tags,
                importance=taxonomy.importance,
                relevance_score=item.relevance_score,
                ai_generated=summary.ai_generated,
                case_parties=candidate.case_parties,
                case_status=candidate.case_status,
                # case_facts es puramente editorial (lo agrega app.editorial
                # sobre ítems de caso ya publicados); el pipeline extractivo
                # nunca lo produce.
                case_facts="",
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
