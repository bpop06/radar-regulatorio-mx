from app.markdown import build_detail_markdown

CARD_BODY = (
    "## Qué se publicó\n\nAcuerdo de la SHCP.\n\n"
    "## Sustancia\n\nSe modifican los lineamientos de operación vigentes.\n\n"
    "## Fuente\n\n[Abrir publicación oficial](https://example.gob.mx/doc)"
)


def test_build_detail_markdown_v2_has_fixed_sections_and_no_legacy_ones():
    markdown = build_detail_markdown(
        title="SHCP modifica lineamientos de operación",
        official_title="Acuerdo por el que se modifican los Lineamientos de Operación",
        source="DOF",
        authority="Secretaría de Hacienda y Crédito Público",
        published_at="2026-07-03",
        categories=("Fiscal",),
        source_url="https://example.gob.mx/doc",
        summary="Resumen editorial de referencia, sin uso porque hay card_body.",
        card_body=CARD_BODY,
    )

    assert markdown.startswith("# SHCP modifica lineamientos de operación")
    assert (
        "**Fecha:** 2026-07-03 · **Fuente:** DOF · "
        "**Órgano:** Secretaría de Hacienda y Crédito Público" in markdown
    )
    assert "## Qué se publicó\n\nAcuerdo de la SHCP." in markdown
    assert "## Sustancia\n\nSe modifican los lineamientos de operación vigentes." in markdown
    assert "## Clasificación" in markdown
    assert "**Materias:** Fiscal." in markdown
    assert "## Fuente oficial" in markdown
    assert "*Acuerdo por el que se modifican los Lineamientos de Operación*" in markdown
    assert "[Abrir documento oficial](https://example.gob.mx/doc)" in markdown

    # La ficha v2 elimina por completo el relleno de v1.
    assert "Revisión" not in markdown
    assert "Resumen generado con IA" not in markdown
    assert "Descripción de origen" not in markdown
    assert "Resumen ejecutivo" not in markdown
    assert "## Hechos del asunto" not in markdown
    assert "## Estado procesal" not in markdown


def test_build_detail_markdown_v2_includes_case_facts_as_paragraphs():
    case_facts = "Primer párrafo con los hechos del asunto.\n\nSegundo párrafo con más contexto."

    markdown = build_detail_markdown(
        title="CIADI admite arbitraje contra México",
        official_title="Caso CIADI No. ARB/26/1",
        source="CIADI",
        authority="CIADI",
        published_at="2026-07-03",
        categories=("Comercio internacional",),
        source_url="https://icsid.worldbank.org/cases/case-detail?CaseNo=ARB/26/1",
        card_body=CARD_BODY,
        case_facts=case_facts,
    )

    assert "## Hechos del asunto" in markdown
    # Los saltos de párrafo se preservan: `case_facts` no pasa por
    # `clean_text`, que colapsaría las líneas en blanco.
    assert case_facts in markdown


def test_build_detail_markdown_v2_includes_case_status_and_parties():
    markdown = build_detail_markdown(
        title="CIADI admite arbitraje contra México",
        official_title="Caso CIADI No. ARB/26/1",
        source="CIADI",
        authority="CIADI",
        published_at="2026-07-03",
        categories=("Comercio internacional",),
        source_url="https://icsid.worldbank.org/cases/case-detail?CaseNo=ARB/26/1",
        card_body=CARD_BODY,
        case_status="En trámite: fase de excepciones preliminares.",
        case_parties="México c. Empresa X",
    )

    assert "## Estado procesal" in markdown
    assert "En trámite: fase de excepciones preliminares." in markdown
    assert "**Partes:** México c. Empresa X" in markdown


def test_build_detail_markdown_v2_omits_case_parties_line_without_parties():
    markdown = build_detail_markdown(
        title="Título",
        official_title="Título oficial",
        source="TFJA",
        authority="Tribunal Federal de Justicia Administrativa",
        published_at="2026-07-03",
        categories=("Contencioso administrativo",),
        source_url="https://example.test/doc",
        card_body=CARD_BODY,
        case_status="Sentencia firme.",
    )

    assert "## Estado procesal" in markdown
    assert "Sentencia firme." in markdown
    assert "**Partes:**" not in markdown


def test_build_detail_markdown_v2_falls_back_without_card_body():
    markdown = build_detail_markdown(
        title="Título",
        official_title="Oficio del SAT sobre contribuyentes",
        source="DOF",
        authority="SECRETARIA DE HACIENDA Y CREDITO PUBLICO",
        published_at="2026-07-03",
        categories=(),
        source_url="https://example.gob.mx/doc",
        document_type="Oficio",
        summary="Resumen extractivo de respaldo con la sustancia mínima necesaria.",
    )

    assert "## Qué se publicó\n\nOficio de SHCP." in markdown
    assert (
        "## Sustancia\n\nResumen extractivo de respaldo con la sustancia mínima necesaria."
        in markdown
    )
    assert "**Materias:** Sin materia clasificada." in markdown
