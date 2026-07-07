from __future__ import annotations

from urllib.parse import quote

from app.text import clean_text


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
    summary: str,
    official_title: str,
    description: str,
    source: str,
    authority: str,
    document_type: str,
    published_at: str,
    categories: tuple[str, ...],
    source_url: str,
    ai_generated: bool,
) -> str:
    category_text = ", ".join(categories) if categories else "Sin materia clasificada"
    description_text = clean_text(description) or clean_text(official_title)
    authority_text = clean_text(authority) or "Autoridad no identificada"
    document_type_text = clean_text(document_type) or "Documento oficial"
    summary_origin = "Sí" if ai_generated else "No"

    return "\n\n".join(
        [
            f"# {clean_text(title)}",
            (
                f"**Fecha de publicación:** {published_at}. "
                f"**Fuente:** {clean_text(source)}. "
                f"**Autoridad:** {authority_text}."
            ),
            "## Resumen ejecutivo",
            clean_text(summary),
            "## Información oficial",
            f"**Título oficial:** {clean_text(official_title)}",
            f"**Tipo de documento:** {document_type_text}",
            f"**Descripción de origen:** {description_text}",
            "## Clasificación",
            f"**Materias:** {category_text}.",
            "## Revisión",
            f"**Resumen generado con IA:** {summary_origin}.",
            "## Fuente oficial",
            f"[Abrir documento oficial]({source_url})",
        ]
    )
