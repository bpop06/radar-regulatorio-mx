from datetime import date

from app.models import Candidate
from app.relevance import classify, is_relevant


def candidate(
    title: str,
    source: str = "DOF",
    *,
    authority: str = "",
    description: str = "",
) -> Candidate:
    return Candidate(
        source=source,
        source_id="1",
        url="https://example.test/1",
        official_title=title,
        description=description,
        published_at=date(2026, 6, 10),
        authority=authority,
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


def test_classifies_lfpa_and_loapf_as_administrative_law():
    result = classify(
        candidate(
            "Decreto por el que se reforman la Ley Federal de Procedimiento Administrativo "
            "y la Ley Orgánica de la Administración Pública Federal"
        )
    )

    assert "Derecho administrativo" in result.categories


def test_classifies_federal_appointment_only_with_action_and_position():
    result = classify(
        candidate("Acuerdo por el que se designa a la nueva Directora General de Aduanas")
    )

    assert "Derecho administrativo" in result.categories
    assert "Nombramientos federales" in result.categories


def test_position_without_appointment_action_is_not_a_nomination():
    result = classify(candidate("Informe anual de la Directora General de Aduanas"))

    assert "Nombramientos federales" not in result.categories


def test_unrelated_appointment_action_and_position_are_not_a_nomination():
    item = candidate(
        "La secretaria toma protesta a una asociación turística",
        description=(
            "La secretaria tomó protesta a la mesa directiva de una asociación estatal. "
            "En otro asunto, el director general presentó estadísticas del sector."
        ),
    )

    result = classify(item)

    assert "Nombramientos federales" not in result.categories


def test_classifies_presidential_appointments_with_diplomatic_positions():
    result = classify(
        candidate(
            "Nombramientos presidenciales son enviados al Senado",
            description=(
                "Se enviaron cuatro nombramientos para ratificación. "
                "Una subsecretaria y tres personas embajadoras fueron designadas."
            ),
        )
    )

    assert "Nombramientos federales" in result.categories


def test_classifies_substitute_for_federal_legal_services_head():
    result = classify(
        candidate(
            "Aviso mediante el cual se designa a la Titular de la Jefatura "
            "de Servicios Jurídicos para suplir ausencias"
        )
    )

    assert "Nombramientos federales" in result.categories


def test_classifies_general_administrative_litigation():
    result = classify(
        candidate(
            "Sentencia definitiva dictada en el juicio de nulidad ante "
            "el Tribunal Federal de Justicia Administrativa"
        )
    )

    assert "Contencioso administrativo" in result.categories
    assert "Contencioso administrativo fiscal" not in result.categories


def test_classifies_tax_administrative_litigation_as_specific_subset():
    result = classify(
        candidate(
            "Sentencia definitiva del Tribunal Federal de Justicia Administrativa "
            "que declara la nulidad de un crédito fiscal"
        )
    )

    assert "Fiscal" in result.categories
    assert "Contencioso administrativo" in result.categories
    assert "Contencioso administrativo fiscal" in result.categories


def test_tax_enforcement_alone_is_not_contentious_litigation():
    result = classify(candidate("Inicio del procedimiento administrativo de ejecución fiscal"))

    assert "Fiscal" in result.categories
    assert "Contencioso administrativo" not in result.categories
    assert "Contencioso administrativo fiscal" not in result.categories


def test_authority_name_does_not_create_subject_matter_match():
    result = classify(
        candidate(
            "Convocatoria para adquirir equipo de oficina",
            authority="Banco Nacional de Comercio Exterior",
        )
    )

    assert "Comercio exterior" not in result.categories


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
