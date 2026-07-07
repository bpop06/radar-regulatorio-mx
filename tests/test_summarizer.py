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

    summary = Summarizer().summarize(item)

    assert 40 <= len(words(summary.summary)) <= 80
    assert "Publicación oficial" in summary.summary
    assert not summary.title.lower().startswith("proyecto de decreto")


def test_fallback_summary_handles_empty_description_and_authority():
    candidate = Candidate(
        source="DOF",
        source_id="9",
        url="https://example.test",
        official_title="",
        description="",
        published_at=date(2026, 6, 1),
        authority="",
        document_type="",
    )
    item = ClassifiedCandidate(
        candidate=candidate,
        categories=(),
        relevance_score=0,
        matched_terms=(),
    )

    summary = Summarizer().summarize(item)

    assert 40 <= len(words(summary.summary)) <= 80
    # "Qué se publicó" nunca arrastra número de acto ni queda vacío.
    assert "## Qué se publicó" in summary.card_body


def test_fallback_what_published_drops_act_number_and_uses_short_name():
    candidate = Candidate(
        source="DOF",
        source_id="10",
        url="https://example.test",
        official_title="Oficio del SAT sobre contribuyentes",
        description="Se comunica un listado de contribuyentes.",
        published_at=date(2026, 6, 1),
        authority="SECRETARIA DE HACIENDA Y CREDITO PUBLICO",
        document_type="Oficio 500-05-2026-16021",
    )
    item = ClassifiedCandidate(
        candidate=candidate,
        categories=("Fiscal",),
        relevance_score=3,
        matched_terms=("sat",),
    )

    summary = Summarizer().summarize(item)

    assert "Oficio de SHCP." in summary.card_body
    assert "500-05-2026" not in summary.card_body.split("## Sustancia")[0]
