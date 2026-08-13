from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class Candidate:
    source: str
    source_id: str
    url: str
    official_title: str
    description: str
    published_at: date
    authority: str = ""
    document_type: str = ""
    case_parties: str = ""
    case_status: str = ""
    # Contrato v8. Los recolectores pueden poblar sólo lo que expone la
    # fuente; el pipeline completa los fallbacks deterministas (URL, fecha y
    # hash) sin inventar contenido editorial.
    canonical_url: str = ""
    official_published_at: date | None = None
    detected_at: datetime | None = None
    content_hash: str = ""
    case_number: str = ""
    case_treaty: str = ""
    case_claim: str = ""
    case_outcome: str = ""
    case_reasoning: str = ""
    case_amount: str = ""
    official_evidence: dict[str, str] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.source.lower()}:{self.source_id}"

    @property
    def searchable_text(self) -> str:
        return " ".join(
            (
                self.official_title,
                self.description,
                self.authority,
                self.document_type,
            )
        )


@dataclass(frozen=True)
class ClassifiedCandidate:
    candidate: Candidate
    categories: tuple[str, ...]
    relevance_score: int
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class Publication:
    id: str
    source_id: str
    source: str
    url: str
    canonical_url: str
    detail_url: str
    official_title: str
    title: str | None
    summary_teaser: str | None
    summary: str | None
    description: str
    detail_markdown: str | None
    card_body: str | None
    published_at: str
    official_published_at: str
    detected_at: str
    first_seen_at: str
    last_seen_at: str
    content_hash: str
    editorial_status: str
    review_reason: str | None
    official_identifiers: dict[str, str]
    official_evidence: dict[str, str]
    authority: str
    document_type: str
    issuing_body: str
    government_branch: str
    jurisdiction: str
    country_or_org: str
    published_year: int
    published_month: int
    published_day: int
    categories: tuple[str, ...]
    topic_tags: tuple[str, ...]
    subtopic_tags: tuple[str, ...]
    importance: int
    relevance_score: int
    ai_generated: bool
    case_number: str = ""
    case_parties: str = ""
    case_status: str = ""
    case_facts: str = ""
    case_treaty: str = ""
    case_claim: str = ""
    case_outcome: str = ""
    case_reasoning: str = ""
    case_amount: str = ""
    # El ciclo editorial v8 distingue la extracción comprobable de la
    # interpretación. Ambos estados viven en la cola privada; sólo un
    # registro completo en ambos sentidos puede formar parte del corte
    # público.
    extraction_status: str = "pending"
    # Ruta relativa al JSON canónico de la ficha. El writer la resuelve al
    # publicar para que pueda cambiarse con --details-dir sin cambiar la
    # identidad del documento.
    detail_data_url: str = ""
    # Ficha editorial estructurada. Los aliases title/summary/card_body se
    # conservan durante la transición v7, pero ésta es la fuente canónica de
    # la nota completa v8.
    executive_summary: list[str] | None = None
    detailed_summary: list[dict[str, Any]] | None = None
    impacts: dict[str, Any] | None = None
    recommended_actions: list[dict[str, Any]] | None = None
    evidence: dict[str, Any] | None = None
    coverage: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["categories"] = list(self.categories)
        result["topic_tags"] = list(self.topic_tags)
        result["subtopic_tags"] = list(self.subtopic_tags)
        return result


@dataclass
class SourceResult:
    source: str
    candidates: list[Candidate] = field(default_factory=list)
    error: str | None = None
    attempts: int = 1
    degraded: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def status_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "status": "error" if self.error else ("degraded" if self.degraded else "ok"),
            "items_found": len(self.candidates),
            "error": self.error,
            "attempts": self.attempts,
            **self.details,
        }
