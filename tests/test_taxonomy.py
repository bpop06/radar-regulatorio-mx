from datetime import date

from app.models import Candidate
from app.relevance import classify
from app.taxonomy import classify_organ, enrich, importance_from_score


def make_candidate(**overrides):
    values = {
        "source": "DOF",
        "source_id": "1",
        "url": "https://dof.gob.mx/nota.php?codigo=1",
        "official_title": "Acuerdo por el que se delegan facultades del SAT.",
        "description": "El Servicio de Administración Tributaria delega facultades.",
        "published_at": date(2026, 7, 3),
        "authority": "SECRETARIA DE HACIENDA Y CREDITO PUBLICO",
        "document_type": "Acuerdo",
    }
    values.update(overrides)
    return Candidate(**values)


def test_classify_organ_resolves_canonical_alias():
    organ, branch = classify_organ(
        "DOF", "SECRETARIA DE HACIENDA Y CREDITO PUBLICO", "Acuerdo del SAT"
    )

    assert organ == "Secretaría de Hacienda y Crédito Público"
    assert branch == "Ejecutivo federal"


def test_classify_organ_falls_back_to_declared_authority():
    organ, branch = classify_organ("DOF", "Comisión Especial de Prueba", "Acuerdo general")

    assert organ == "Comisión Especial de Prueba"
    assert branch == "Ejecutivo federal"


def test_enrich_produces_national_taxonomy_with_date_facets():
    item = classify(make_candidate())
    taxonomy = enrich(item)

    assert taxonomy.jurisdiction == "nacional"
    assert taxonomy.country_or_org == "México"
    assert (taxonomy.published_year, taxonomy.published_month, taxonomy.published_day) == (
        2026,
        7,
        3,
    )
    assert "Acuerdo" in taxonomy.topic_tags
    assert 1 <= taxonomy.importance <= 5
    assert taxonomy.subtopic_tags


def test_enrich_marks_international_sources():
    item = classify(
        make_candidate(
            source="USTR",
            authority="Office of the United States Trade Representative",
            official_title="USTR announces tariff review for Mexican imports",
            description="Trade measure review under USMCA import tariff arancel.",
        )
    )
    taxonomy = enrich(item)

    assert taxonomy.jurisdiction == "internacional"
    assert taxonomy.country_or_org == "EEUU"


def test_importance_from_score_buckets():
    assert importance_from_score(0) == 1
    assert importance_from_score(3) == 2
    assert importance_from_score(4) == 3
    assert importance_from_score(7) == 4
    assert importance_from_score(9) == 5
