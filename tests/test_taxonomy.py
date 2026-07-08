from datetime import date

from app.models import Candidate
from app.relevance import classify
from app.taxonomy import (
    classify_organ,
    enrich,
    importance_from_score,
    primary_categories,
    short_organ_name,
)


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


def test_primary_categories_shcp_authority_passes_as_central_administration():
    # Sin categoría fina pero con autoridad SHCP: siempre entra como
    # Administración centralizada (coordina con is_relevant).
    assert primary_categories(
        (), "Ejecutivo federal", "SECRETARIA DE HACIENDA Y CREDITO PUBLICO", "DOF"
    ) == ("Administración centralizada",)


def test_primary_categories_intellectual_property_follows_organ_branch():
    # IMPI es organismo descentralizado -> Administración descentralizada.
    assert primary_categories(
        ("Propiedad intelectual",),
        "Organismo descentralizado",
        "Instituto Mexicano de la Propiedad Industrial",
        "IMPI",
    ) == ("Administración descentralizada",)
    # La misma materia en un órgano centralizado cae en centralizada.
    assert primary_categories(
        ("Normalización",), "Ejecutivo federal", "Secretaría de Economía", "DOF"
    ) == ("Administración centralizada",)


def test_primary_categories_uses_source_default_for_international_sources():
    assert primary_categories((), "Organismo internacional", "", "CIADI") == (
        "Comercio internacional",
    )
    assert primary_categories((), "Organismo internacional", "", "CPI") == ("Penal",)


def test_primary_categories_collapses_initiative_to_legislative_process():
    primaries = primary_categories(
        ("Fiscal", "Iniciativa"), "Legislativo federal", "Senado de la República", "Senado"
    )

    assert "Proceso legislativo" in primaries
    assert "Fiscal" in primaries


def test_primary_categories_maps_customs_and_contentious_fiscal():
    assert primary_categories(
        ("Aduanero", "Comercio exterior"), "Ejecutivo federal", "ANAM", "DOF"
    ) == ("Comercio exterior",)
    assert primary_categories(
        ("Contencioso administrativo fiscal",),
        "Órgano jurisdiccional",
        "Tribunal Federal de Justicia Administrativa",
        "DOF",
    ) == ("Fiscal",)


def test_short_organ_name_resolves_catalog_and_falls_back():
    assert short_organ_name("DOF", "SECRETARIA DE HACIENDA Y CREDITO PUBLICO", "") == "SHCP"
    assert short_organ_name("IMPI", "", "Aviso del IMPI sobre marcas") == "IMPI"
    assert short_organ_name("DOF", "Comisión Especial de Prueba", "") == "Comisión"
    assert short_organ_name("CIADI", "", "") == "CIADI"


def test_enrich_exposes_primary_categories_and_keeps_fine_in_topic_tags():
    item = classify(make_candidate())
    taxonomy = enrich(item)

    assert taxonomy.primary_categories
    # Las categorías finas de classify siguen presentes como etiquetas.
    for fine in item.categories:
        assert fine in taxonomy.topic_tags


def test_classify_organ_resolves_imss_fgr_issste_inpi_and_semar():
    # Regresión #19: estos órganos caían al fallback en mayúsculas crudas
    # (p.ej. "INSTITUTO MEXICANO DEL SEGURO SOCIAL") en vez de resolver al
    # catálogo canónico.
    assert classify_organ("Gob.mx APF", "IMSS", "") == (
        "Instituto Mexicano del Seguro Social",
        "Organismo descentralizado",
    )
    assert classify_organ(
        "DOF", "FISCALIA GENERAL DE LA REPUBLICA", ""
    ) == (
        "Fiscalía General de la República",
        "Organismo autónomo",
    )
    assert classify_organ("Gob.mx APF", "ISSSTE", "")[0] == (
        "Instituto de Seguridad y Servicios Sociales de los Trabajadores del Estado"
    )
    assert classify_organ("Gob.mx APF", "INPI", "")[0] == (
        "Instituto Nacional de los Pueblos Indígenas"
    )
    assert classify_organ("Gob.mx APF", "Secretaría de Marina", "") == (
        "Secretaría de Marina",
        "Ejecutivo federal",
    )
    assert short_organ_name("Gob.mx APF", "Secretaría de Marina", "") == "Semar"


def test_classify_organ_prefers_secretaria_over_poder_ejecutivo_prefix():
    # El encabezado del DOF suele traer "PODER EJECUTIVO <SECRETARIA...>";
    # el alias genérico de Presidencia ("poder ejecutivo") no debe ganarle
    # a la secretaría específica que sí está en el catálogo.
    organ, _ = classify_organ("DOF", "PODER EJECUTIVO SECRETARIA DE MARINA", "")
    assert organ == "Secretaría de Marina"


def test_alias_matching_requires_word_boundaries():
    # "hIMFg" y "cONUee" contienen los alias cortos "imf"/"onu" como
    # subcadena, pero NO son el FMI ni la ONU: el matching debe exigir
    # frontera de palabra (regresión de la corrida en vivo v4).
    organ, branch = classify_organ(
        "Gob.mx APF",
        "Instituto Nacional de Perinatología",
        "Aviso de la consulta del HIMFG",
    )
    assert organ != "Fondo Monetario Internacional"

    organ, _ = classify_organ("Gob.mx APF", "Portal federal CONUEE", "")
    assert organ != "Organización de las Naciones Unidas"

    # Los alias legítimos con frontera real siguen funcionando.
    assert short_organ_name("Gob.mx APF", "", "Informe del FMI sobre México") == "FMI"
