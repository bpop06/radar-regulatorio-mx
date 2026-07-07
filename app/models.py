from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date


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
    source: str
    url: str
    detail_url: str
    official_title: str
    title: str
    summary: str
    description: str
    detail_markdown: str
    card_body: str
    published_at: str
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

    def status_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "status": "error" if self.error else "ok",
            "items_found": len(self.candidates),
            "error": self.error,
            "attempts": self.attempts,
        }
