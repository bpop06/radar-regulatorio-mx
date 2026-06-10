from datetime import date

from app.models import Candidate, ClassifiedCandidate
from app.summarizer import Summarizer
from app.text import words


def test_fallback_summary_is_readable_and_exact():
    candidate = Candidate(
        source="Senado",
        source_id="1",
        url="https://example.test",
        official_title=(
            "Proyecto de decreto por el que se adiciona una fracción a "
            "la Ley del Impuesto Sobre la Renta."
        ),
        description="Deducción fiscal para gastos de cuidados.",
        published_at=date(2026, 6, 1),
        authority="Senado de la República",
        document_type="Iniciativa de Ley",
    )
    item = ClassifiedCandidate(
        candidate=candidate,
        categories=("Fiscal", "Iniciativa"),
        relevance_score=6,
        matched_terms=("impuesto", "iniciativa"),
    )

    summary = Summarizer(api_key=None, model="gpt-5.5").summarize(item)

    assert len(words(summary.summary)) == 30
    assert "Publicación oficial" in summary.summary
    assert not summary.title.lower().startswith("proyecto de decreto")
