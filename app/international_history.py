from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.edition import (
    PublishError,
    load_manifested_items,
    official_content_hash,
)
from app.sources.international import ExtractedCase, extract_international_case
from app.validation import validate_site_artifacts

SUPPORTED_SOURCES = {"CIJ", "CPI"}
OFFICIAL_HOSTS = {
    "CIJ": {"icj-cij.org", "www.icj-cij.org"},
    "CPI": {"icc-cpi.int", "www.icc-cpi.int"},
}
CASE_FIELDS = {
    "case_number": "number",
    "case_parties": "parties",
    "case_claim": "claim",
    "case_status": "status",
    "case_outcome": "outcome",
    "case_reasoning": "reasoning",
    "case_amount": "amount",
}
EDITORIAL_FIELDS = ("title", "summary_teaser", "summary", "card_body", "detail_markdown")


class InternationalHistoryError(ValueError):
    """La migración histórica no puede ejecutarse sobre el corte confirmado."""


def rebuild_manifested_international_history(
    publications_path: Path,
) -> list[dict[str, Any]]:
    """Enriquece CIJ/CPI del manifiesto sin consultar red ni alterar el corte móvil.

    Sólo devuelve fichas cuyo contenido oficial almacenado aporta nueva
    evidencia literal. El llamador las transporta mediante
    ``_historical_items`` para que el writer regenere ficha, nota e índice
    mensual dentro de su transacción normal.
    """

    publications_path = Path(publications_path)
    manifest_path = publications_path.with_name("manifest.json")
    report = validate_site_artifacts(manifest_path)
    if not report.ok:
        raise InternationalHistoryError(
            "el corte base no pasa la validación transaccional: "
            + "; ".join(report.errors[:5])
        )
    try:
        manifested = load_manifested_items(publications_path)
    except PublishError as exc:
        raise InternationalHistoryError(
            f"no se pudieron cargar las fichas permanentes: {exc}"
        ) from exc

    migrated = [
        enriched
        for item in manifested.values()
        if (enriched := enrich_manifested_international_item(item)) is not None
    ]
    return sorted(migrated, key=lambda item: str(item.get("id") or ""))


def enrich_manifested_international_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Agrega únicamente valores ausentes sustentados por texto oficial literal."""

    source = item.get("source")
    if source not in SUPPORTED_SOURCES:
        return None
    title = item.get("official_title")
    description = item.get("description")
    official_url = item.get("url")
    if not all(isinstance(value, str) and value.strip() for value in (title, description)):
        return None
    if not isinstance(official_url, str) or not _is_official_url(source, official_url):
        return None

    extracted = extract_international_case(source, title, description, official_url)
    # Un importe o un verbo procesal aislado en una noticia institucional no
    # basta para convertirla en ficha de caso. Se exige número o carátula
    # explícitos antes de admitir cualquier enriquecimiento.
    if not extracted.number and not extracted.parties:
        return None

    migrated = deepcopy(item)
    changed = _merge_case_fields(migrated, extracted)
    changed = _merge_evidence(migrated, extracted, official_url) or changed
    changed = _merge_identifiers(migrated, extracted) or changed
    if not changed:
        return None

    previous_hash = migrated.get("content_hash")
    migrated["content_hash"] = official_content_hash(migrated)
    if migrated["content_hash"] != previous_hash:
        _invalidate_editorial(migrated)
    return migrated


def _merge_case_fields(item: dict[str, Any], extracted: ExtractedCase) -> bool:
    changed = False
    for field, attribute in CASE_FIELDS.items():
        value = str(getattr(extracted, attribute, "") or "").strip()
        current = item.get(field)
        if value and (not isinstance(current, str) or not current.strip()):
            item[field] = value
            changed = True
    return changed


def _merge_evidence(
    item: dict[str, Any], extracted: ExtractedCase, official_url: str
) -> bool:
    original = item.get("official_evidence")
    evidence = deepcopy(original) if isinstance(original, dict) else {}
    changed = not isinstance(original, dict)
    for key, raw_value in {"primary_url": official_url, **extracted.evidence}.items():
        value = raw_value.strip() if isinstance(raw_value, str) else ""
        current = evidence.get(key)
        if value and (not isinstance(current, str) or not current.strip()):
            evidence[key] = value
            changed = True
    if changed:
        item["official_evidence"] = evidence
    return changed


def _merge_identifiers(item: dict[str, Any], extracted: ExtractedCase) -> bool:
    if not extracted.number:
        return False
    original = item.get("official_identifiers")
    identifiers = deepcopy(original) if isinstance(original, dict) else {}
    current = identifiers.get("case_number")
    if isinstance(current, str) and current.strip():
        return False
    identifiers["case_number"] = extracted.number
    item["official_identifiers"] = identifiers
    return True


def _invalidate_editorial(item: dict[str, Any]) -> None:
    item["editorial_status"] = "needs_review"
    item["review_reason"] = "La evidencia oficial cambió; requiere una nueva revisión editorial."
    for field in EDITORIAL_FIELDS:
        item[field] = None
    item["case_facts"] = ""
    item["ai_generated"] = False


def _is_official_url(source: str, value: str) -> bool:
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and not parts.username
        and not parts.password
        and (parts.hostname or "").casefold() in OFFICIAL_HOSTS[source]
    )
