from __future__ import annotations

import asyncio
import hashlib
import json
import ssl
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import truststore

from app.config import Settings
from app.edition import edition_date_from_generated_at, prepare_payload
from app.models import Candidate, ClassifiedCandidate, Publication, SourceResult
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
from app.taxonomy import enrich


async def collect(
    settings: Settings,
    days: int | None = None,
    dry_run: bool = False,
    rebuild_stateful_history: bool = False,
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
            # Los recolectores de estado sólo proponen el siguiente snapshot.
            # El writer lo confirma junto con el resto del corte; ni dry-run
            # ni una validación fallida pueden consumir novedades.
            IcsidCollector(
                client,
                persist_snapshot=False,
                force_bootstrap=rebuild_stateful_history,
            ),
            AnamCollector(client),
            TfjaCollector(client),
            OmcCollector(client),
            TmecCollector(
                client,
                persist_snapshot=False,
                force_bootstrap=rebuild_stateful_history,
            ),
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

    unique: dict[tuple[str, str], Candidate] = {}
    for result in results:
        for candidate in result.candidates:
            key = _stable_identity(candidate)
            current = unique.get(key)
            if current is None or len(candidate.description) > len(current.description):
                unique[key] = candidate

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

    generated_at = datetime.now(UTC)
    publications = [_publication_from_classified(item, generated_at) for item in classified]

    # Los inventarios de bootstrap se enriquecen con el mismo contrato que
    # las novedades, pero no pasan por el filtro temporal ni entran a
    # `items`: sólo alimentan el archivo permanente en el writer.
    historical_candidates = _deduplicate(
        candidate
        for collector in collectors
        for candidate in getattr(collector, "historical_candidates", ())
    )
    historical = [
        _publication_from_classified(classify(candidate), generated_at)
        for candidate in historical_candidates
    ]

    pending_state: dict[str, object] = {}
    for collector in collectors:
        proposal = getattr(collector, "pending_state", None)
        if proposal is not None:
            pending_state[_state_name(collector.source)] = proposal

    payload: dict[str, object] = {
        "generated_at": generated_at.isoformat(),
        "lookback_days": lookback,
        "total_items": len(publications),
        "sources": [result.status_dict() for result in results],
        "items": [publication.to_dict() for publication in publications],
    }
    if pending_state:
        # Campo de transporte interno. `write_site_artifacts` lo excluye del
        # JSON público y sólo lo confirma dentro de la transacción del corte.
        payload["_pending_state"] = pending_state
    if historical:
        # Transporte privado: `write_site_artifacts` lo retira antes de crear
        # publications.json/edition.json y lo mezcla sólo al archivo estable.
        payload["_historical_items"] = [item.to_dict() for item in historical]
    return prepare_payload(payload, settings.local_timezone)


def _publication_from_classified(
    item: ClassifiedCandidate, generated_at: datetime
) -> Publication:
    candidate = item.candidate
    taxonomy = enrich(item)
    source_id = candidate.source_id.strip() or _canonical_url(candidate.url)
    publication_id = f"{candidate.source.lower()}:{source_id}"
    canonical_url = candidate.canonical_url.strip() or _canonical_url(candidate.url)
    official_date = candidate.official_published_at or candidate.published_at
    published_at = official_date.isoformat()
    detected = candidate.detected_at or generated_at
    detected_at = (
        detected.astimezone(UTC).isoformat()
        if detected.tzinfo
        else detected.replace(tzinfo=UTC).isoformat()
    )
    content_hash = candidate.content_hash or _candidate_content_hash(
        candidate,
        source_id=source_id,
        canonical_url=canonical_url,
        official_published_at=published_at,
    )
    item_key = _item_key(publication_id)
    official_identifiers = {"source_id": source_id}
    if candidate.case_number:
        official_identifiers["case_number"] = candidate.case_number
    evidence = dict(candidate.official_evidence)
    # La evidencia conserva anclas semánticas; canonical_url se reserva para
    # identidad y deduplicación estable.
    evidence.setdefault("primary_url", candidate.url)
    return Publication(
        id=publication_id,
        source_id=source_id,
        source=candidate.source,
        url=candidate.url,
        canonical_url=canonical_url,
        detail_url=f"notas/{item_key}.html",
        official_title=candidate.official_title,
        title=None,
        summary_teaser=None,
        summary=None,
        description=candidate.description,
        detail_markdown=None,
        card_body=None,
        published_at=published_at,
        official_published_at=published_at,
        detected_at=detected_at,
        first_seen_at=detected_at,
        last_seen_at=detected_at,
        content_hash=content_hash,
        editorial_status="needs_review",
        review_reason="Contenido nuevo o modificado; requiere revisión editorial.",
        official_identifiers=official_identifiers,
        official_evidence=evidence,
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
        ai_generated=False,
        case_number=candidate.case_number,
        case_parties=candidate.case_parties,
        case_status=candidate.case_status,
        case_facts="",
        case_treaty=candidate.case_treaty,
        case_claim=candidate.case_claim,
        case_outcome=candidate.case_outcome,
        case_reasoning=candidate.case_reasoning,
        case_amount=candidate.case_amount,
    )


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
            diagnostics = getattr(collector, "diagnostics", None)
            warnings = list(getattr(diagnostics, "warnings", ()) or ())
            validated_endpoints = int(getattr(diagnostics, "validated_endpoints", 0) or 0)
            if validated_endpoints < 1:
                warnings.append(
                    "ningún endpoint superó transporte, tipo de contenido y estructura"
                )
            degraded = getattr(diagnostics, "status", "ok") == "degraded" or bool(warnings)
            details: dict[str, object] = {}
            if warnings:
                details["warnings"] = warnings
            details["validated_endpoints"] = validated_endpoints
            return SourceResult(
                source=collector.source,
                candidates=candidates,
                attempts=current_attempt,
                degraded=degraded,
                details=details,
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
    selected: dict[tuple[str, str], Candidate] = {}
    for candidate in candidates:
        key = _stable_identity(candidate)
        current = selected.get(key)
        if current is None or len(candidate.description) > len(current.description):
            selected[key] = candidate
    return list(selected.values())


def _stable_identity(candidate: Candidate) -> tuple[str, str]:
    """Identidad v8: fuente+source_id; URL canónica sólo como respaldo."""
    source = candidate.source.strip().casefold()
    identifier = candidate.source_id.strip()
    if not identifier:
        identifier = candidate.canonical_url.strip() or _canonical_url(candidate.url)
    return source, identifier


def _canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def _candidate_content_hash(
    candidate: Candidate,
    *,
    source_id: str,
    canonical_url: str,
    official_published_at: str,
) -> str:
    official = {
        "source": candidate.source,
        "source_id": source_id,
        "canonical_url": canonical_url,
        "official_title": candidate.official_title,
        "description": candidate.description,
        "official_published_at": official_published_at,
        "authority": candidate.authority,
        "document_type": candidate.document_type,
        "case_number": candidate.case_number,
        "case_parties": candidate.case_parties,
        "case_status": candidate.case_status,
        "case_treaty": candidate.case_treaty,
        "official_evidence": candidate.official_evidence,
    }
    encoded = json.dumps(
        official, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _item_key(publication_id: str) -> str:
    return hashlib.sha256(publication_id.encode()).hexdigest()[:24]


def _state_name(source: str) -> str:
    return {
        "CIADI": "icsid",
        "Secretariado T-MEC": "tmec",
    }.get(source, source.casefold().replace(" ", "-"))


def _local_today(timezone_name: str) -> date:
    return edition_date_from_generated_at(datetime.now(UTC).isoformat(), timezone_name)
