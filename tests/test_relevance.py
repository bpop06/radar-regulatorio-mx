from datetime import date

from app.models import Candidate
from app.relevance import classify, is_relevant


def candidate(title: str, source: str = "DOF") -> Candidate:
    return Candidate(
        source=source,
        source_id="1",
        url="https://example.test/1",
        official_title=title,
        description="",
        published_at=date(2026, 6, 10),
    )


def test_classifies_customs_and_foreign_trade():
    result = classify(
        candidate(
            "Primera Resolución de Modificaciones "
            "a las Reglas Generales de Comercio Exterior"
        )
    )

    assert "Aduanero" in result.categories
    assert "Comercio exterior" in result.categories
    assert result.relevance_score >= 2


def test_classifies_legislative_tax_initiative():
    result = classify(
        candidate(
            "Iniciativa con proyecto de decreto que reforma el Código Fiscal de la Federación",
            source="Senado",
        )
    )

    assert "Fiscal" in result.categories
    assert "Iniciativa" in result.categories
    assert result.relevance_score >= 4


def test_unrelated_item_has_no_categories():
    result = classify(candidate("Convenio cultural para actividades deportivas municipales"))

    assert result.categories == ()


def test_initiative_needs_a_substantive_legal_category():
    result = classify(candidate("Proyecto de decreto que reforma la Ley General de Cultura"))

    assert "Iniciativa" in result.categories
    assert not is_relevant(result, minimum_score=2)


def test_fiscalia_does_not_match_fiscal():
    result = classify(candidate("Requisitos para ocupar la Fiscalía General de la República"))

    assert "Fiscal" not in result.categories
