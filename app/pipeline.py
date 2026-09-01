from __future__ import annotations

import asyncio
import hashlib
import json
import ssl
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import truststore

from app.config import Settings
from app.edition import edition_date_from_generated_at, prepare_payload
from app.identity import official_semantic_alias, record_identity_url, source_priority
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


def _dof_previous_day_reviewed(
    results: list[SourceResult], since: date, local_today: date
) -> bool:
    """Indica si DOF validó un corte que alcanza al menos el día anterior."""
    if since > local_today - timedelta(days=1):
        return False

    for result in results:
        validated_endpoints = result.details.get("validated_endpoints")
        if (
            result.source == "DOF"
            and result.error is None
            and result.degraded is False
            and isinstance(validated_endpoints, int)
            and not isinstance(validated_endpoints, bool)
            and validated_endpoints >= 1
        ):
            return True
    return False


async def collect(
    settings: Settings,
    days: int | None = None,
    dry_run: bool = False,
    rebuild_stateful_history: bool = False,
) -> dict[str, object]:
    lookback = days if days is not None else settings.lookback_days
    local_today = _local_today(settings.local_timezone)
    since = local_today - timedelta(days=lookback)
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
        results = await _collect_results(
            collectors,
            since,
            attempts=source_retries,
            backoff_seconds=retry_backoff,
            source_deadline_seconds=settings.source_deadline_seconds,
            collection_deadline_seconds=settings.collection_deadline_seconds,
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
    edition_date = edition_date_from_generated_at(
        generated_at.isoformat(), settings.local_timezone
    )
    publications = [
        _publication_from_classified(
            item,
            generated_at,
            edition_date=edition_date,
            baseline_inventory=rebuild_stateful_history,
        )
        for item in classified
    ]

    # Los inventarios de bootstrap se enriquecen con el mismo contrato que
    # las novedades, pero no pasan por el filtro temporal ni entran a
    # `items`: sólo alimentan el archivo permanente en el writer.
    historical_candidates = _deduplicate(
        candidate
        for collector in collectors
        for candidate in getattr(collector, "historical_candidates", ())
    )
    historical = [
        _publication_from_classified(
            classify(candidate), generated_at, edition_date=edition_date
        )
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
        "collection_notes": {
            "dof_previous_day_reviewed": _dof_previous_day_reviewed(
                results, since, local_today
            )
        },
        "items": [publication.to_dict() for publication in publications],
    }
    if rebuild_stateful_history:
        # Transporte privado del relanzamiento: permite corregir un corte v8
        # previo que hubiera marcado todo el inventario móvil como detectado
        # hoy, sin apagar cambios explícitos de fuentes con estado.
        payload["_baseline_inventory"] = True
        payload["_explicit_detection_ids"] = [
            publication.id
            for classified_item, publication in zip(classified, publications, strict=True)
            if classified_item.candidate.detected_at is not None
        ]
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
    item: ClassifiedCandidate,
    generated_at: datetime,
    *,
    edition_date: date | None = None,
    baseline_inventory: bool = False,
) -> Publication:
    candidate = item.candidate
    taxonomy = enrich(item)
    source_id = candidate.source_id.strip() or _canonical_url(candidate.url)
    publication_id = f"{candidate.source.lower()}:{source_id}"
    canonical_url = candidate.canonical_url.strip() or _canonical_url(candidate.url)
    official_date = candidate.official_published_at or candidate.published_at
    published_at = official_date.isoformat()
    if candidate.detected_at is not None:
        # Los recolectores de estado usan este campo para anunciar hoy un
        # cambio aun cuando la fecha oficial del expediente sea histórica.
        detected = candidate.detected_at
    elif baseline_inventory and edition_date is not None and official_date < edition_date:
        # Una ventana móvil es inventario, no un digest de novedades. En el
        # bootstrap, los actos antiguos sin señal explícita de detección se
        # anclan a su fecha oficial para no publicar los 31 días como “hoy”.
        detected = datetime.combine(
            official_date,
            datetime.min.time().replace(hour=12),
            tzinfo=UTC,
        )
    else:
        detected = generated_at
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


async def _collect_results(
    collectors: list[object],
    since: date,
    *,
    attempts: int,
    backoff_seconds: float,
    source_deadline_seconds: float,
    collection_deadline_seconds: float,
) -> list[SourceResult]:
    """Bound each official collector and the whole fan-out without false green.

    A stalled official portal becomes an explicit degraded source rather than
    holding the remaining 17 sources indefinitely.  The collector object may
    expose ``unfinished_work`` to report queue counts honestly on cancellation.
    """

    per_source = max(0.1, float(source_deadline_seconds))
    total_budget = max(0.1, float(collection_deadline_seconds))
    pairs = [
        (
            collector,
            asyncio.create_task(
                _collect_with_deadline(
                    collector,
                    since,
                    attempts=attempts,
                    backoff_seconds=backoff_seconds,
                    deadline_seconds=per_source,
                )
            ),
        )
        for collector in collectors
    ]
    done, pending = await asyncio.wait(
        [task for _, task in pairs], timeout=total_budget
    )
    task_results: dict[asyncio.Task[SourceResult], SourceResult] = {}
    for task in done:
        try:
            task_results[task] = task.result()
        except Exception as exc:  # Defensive boundary around an unexpected task failure.
            collector = next(
                candidate for candidate, candidate_task in pairs if candidate_task is task
            )
            task_results[task] = SourceResult(
                source=str(getattr(collector, "source", "Fuente desconocida")),
                error=f"{type(exc).__name__}: {str(exc)[:240]}",
                attempts=attempts,
            )
    if pending:
        for collector, task in pairs:
            if task not in pending:
                continue
            task.cancel()
            task_results[task] = _deadline_result(
                collector,
                attempts=attempts,
                deadline_seconds=total_budget,
                scope="colección completa",
            )
        await asyncio.gather(*pending, return_exceptions=True)
    return [task_results[task] for _, task in pairs]


async def _collect_with_deadline(
    collector: object,
    since: date,
    *,
    attempts: int,
    backoff_seconds: float,
    deadline_seconds: float,
) -> SourceResult:
    try:
        return await asyncio.wait_for(
            _collect_source(collector, since, attempts, backoff_seconds),
            timeout=deadline_seconds,
        )
    except TimeoutError:
        return _deadline_result(
            collector,
            attempts=attempts,
            deadline_seconds=deadline_seconds,
            scope="fuente",
        )


def _deadline_result(
    collector: object,
    *,
    attempts: int,
    deadline_seconds: float,
    scope: str,
) -> SourceResult:
    source = str(getattr(collector, "source", "Fuente desconocida"))
    diagnostics = getattr(collector, "diagnostics", None)
    validated_endpoints = int(getattr(diagnostics, "validated_endpoints", 0) or 0)
    warning = f"{scope} agotó su presupuesto de {deadline_seconds:g} segundos"
    if hasattr(collector, "mark_degraded"):
        collector.mark_degraded(warning)
    details: dict[str, object] = {
        "warnings": [warning],
        "validated_endpoints": validated_endpoints,
        "deadline_seconds": deadline_seconds,
        "deadline_scope": scope,
    }
    unfinished = getattr(collector, "unfinished_work", None)
    if isinstance(unfinished, dict) and unfinished:
        details["unfinished_work"] = dict(unfinished)
    return SourceResult(
        source=source,
        attempts=attempts,
        degraded=True,
        details=details,
    )


def write_output(payload: dict[str, object], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _deduplicate(candidates) -> list[Candidate]:
    """Deduplica por identidad oficial sin recurrir a título+fecha.

    La identidad primaria sigue siendo ``source + source_id``. Después se
    conectan las copias que exponen la misma URL canónica, aunque provengan de
    recolectores distintos (por ejemplo IMPI/Gob.mx o DOF/SNICE). Las anclas
    de la Gaceta de Diputados son parte de la identidad del asunto y por ello
    se conservan. Finalmente, Gob.mx tiene una equivalencia oficial y acotada
    entre las rutas ``/impi/prensa/`` y ``/impi/es/articulos/``; esta permite
    colapsar el espejo de artículo que Gob.mx genera para el mismo comunicado.
    """

    primary: dict[tuple[str, str], Candidate] = {}
    for candidate in candidates:
        key = _stable_identity(candidate)
        current = primary.get(key)
        if current is None or _same_source_rank(candidate) > _same_source_rank(current):
            primary[key] = candidate

    records = list(primary.values())
    if len(records) < 2:
        return records

    parents = list(range(len(records)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    url_aliases: dict[str, list[int]] = defaultdict(list)
    semantic_aliases: dict[str, int] = {}
    for index, candidate in enumerate(records):
        canonical_url = record_identity_url(candidate)
        if canonical_url:
            # La URL es respaldo global entre recolectores. Dentro de una
            # misma fuente, dos source_id oficiales distintos conservan su
            # identidad aunque por error expongan una URL genérica igual.
            for previous in url_aliases[canonical_url]:
                if records[previous].source.casefold() != candidate.source.casefold():
                    union(index, previous)
            url_aliases[canonical_url].append(index)

        semantic_alias = official_semantic_alias(candidate)
        if semantic_alias:
            previous = semantic_aliases.setdefault(semantic_alias, index)
            union(index, previous)

    groups: dict[int, list[Candidate]] = defaultdict(list)
    for index, candidate in enumerate(records):
        groups[find(index)].append(candidate)

    winners = [max(group, key=_cross_source_rank) for group in groups.values()]
    return sorted(
        winners,
        key=lambda candidate: (
            candidate.source.casefold(),
            candidate.source_id.casefold(),
            candidate.url,
        ),
    )


def _stable_identity(candidate: Candidate) -> tuple[str, str]:
    """Identidad v8: fuente+source_id; URL canónica sólo como respaldo."""
    source = candidate.source.strip().casefold()
    identifier = candidate.source_id.strip()
    if not identifier:
        identifier = candidate.canonical_url.strip() or _canonical_url(candidate.url)
    return source, identifier


def _same_source_rank(candidate: Candidate) -> tuple[int, int, str, str]:
    """Elige de forma reproducible la copia más informativa de una identidad."""

    return (
        len(candidate.description.strip()),
        len(candidate.official_evidence),
        candidate.canonical_url or candidate.url,
        candidate.official_title,
    )


def _cross_source_rank(candidate: Candidate) -> tuple[int, int, int, str, str]:
    """Prefiere el recolector oficial específico frente a un agregador."""

    return (
        source_priority(candidate.source),
        len(candidate.description.strip()),
        len(candidate.official_evidence),
        candidate.source.casefold(),
        candidate.source_id.casefold(),
    )


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
