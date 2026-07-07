from __future__ import annotations

from urllib.parse import quote

from app.taxonomy import short_organ_name
from app.text import clean_text
from app.validation import (
    SOURCE_OFICIAL_SECTION,
    SUBSTANCE_SECTION,
    WHAT_PUBLISHED_SECTION,
    card_section_text,
)


def detail_url(publication_id: str) -> str:
    return f"ficha.html?id={quote(publication_id, safe='')}"


def build_card_body(
    *,
    what_published: str,
    substance: str,
    source_url: str,
) -> str:
    """Cuerpo editorial de la ficha con jerarquía fija de secciones."""
    return "\n\n".join(
        [
            "## Qué se publicó",
            clean_text(what_published),
            "## Sustancia",
            clean_text(substance),
            "## Fuente",
            f"[Abrir publicación oficial]({source_url})",
        ]
    )


def build_detail_markdown(
    *,
    title: str,
    official_title: str,
    source: str,
    authority: str,
    published_at: str,
    categories: tuple[str, ...],
    source_url: str,
    document_type: str = "",
    summary: str = "",
    card_body: str = "",
    case_facts: str = "",
    case_status: str = "",
    case_parties: str = "",
) -> str:
    """Ficha única v2 (contrato v6): sustantiva, sin relleno ni secciones
    duplicadas ("Revisión"/"Resumen generado con IA" y la descripción de
    origen desaparecen por completo frente a v1).

    "Qué se publicó" y "Sustancia" se extraen del `card_body` ya redactado
    (misma prosa que ve la tarjeta, sin reescribirla). El pipeline extractivo
    siempre trae `card_body` porque `Summarizer` lo genera incluso sin capa
    editorial; el fallback a `summary`/`document_type` solo cubre llamadas
    fuera de ese flujo (pruebas, migraciones) para no publicar una ficha
    vacía si algún día falta.
    """
    what_published = card_section_text(card_body, WHAT_PUBLISHED_SECTION) if card_body else ""
    if not what_published:
        organ_short = short_organ_name(source, authority, official_title)
        document_type_text = clean_text(document_type) or "Documento oficial"
        what_published = f"{document_type_text} de {organ_short}."

    substance = card_section_text(card_body, SUBSTANCE_SECTION) if card_body else ""
    if not substance:
        substance = clean_text(summary) or "Sin sustancia editorial disponible."

    category_text = ", ".join(categories) if categories else "Sin materia clasificada"
    authority_text = clean_text(authority) or "Autoridad no identificada"

    sections = [
        f"# {clean_text(title)}",
        (
            f"**Fecha:** {published_at} · **Fuente:** {clean_text(source)} · "
            f"**Órgano:** {authority_text}"
        ),
        WHAT_PUBLISHED_SECTION,
        clean_text(what_published),
        SUBSTANCE_SECTION,
        clean_text(substance),
    ]

    # "Hechos del asunto" y "Estado procesal" son condicionales: solo los
    # casos contenciosos (CIADI, TFJA...) traen `case_facts`/`case_status`;
    # el resto de las publicaciones no tiene un "asunto" que narrar.
    if case_facts.strip():
        sections.append("## Hechos del asunto")
        # `case_facts` es markdown de párrafos redactado por la editorial
        # (sin encabezados propios): no pasa por `clean_text`, que colapsaría
        # los saltos de línea entre párrafos a un solo espacio.
        sections.append(case_facts.strip())

    if case_status.strip():
        sections.append("## Estado procesal")
        status_block = clean_text(case_status)
        if case_parties.strip():
            status_block += f"\n\n**Partes:** {clean_text(case_parties)}"
        sections.append(status_block)

    sections.extend(
        [
            "## Clasificación",
            f"**Materias:** {category_text}.",
            SOURCE_OFICIAL_SECTION,
            f"*{clean_text(official_title)}*\n\n[Abrir documento oficial]({source_url})",
        ]
    )

    return "\n\n".join(sections)
