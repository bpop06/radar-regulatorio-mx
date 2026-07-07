from __future__ import annotations

import re

from app.sources.international import RssCollector


class AnamCollector(RssCollector):
    """Comunicados y boletines de la ANAM desde su sitio propio (WordPress).

    La agencia volvió a estar en línea (estuvo caída en la auditoría v4) y
    publica `sitemap.rss`: un feed RSS 2.0 generado por su plugin SEO con las
    páginas modificadas recientemente, título real y pubDate. Incluye los
    boletines técnicos informativos que NO llegan al DOF. Verificado en vivo
    desde este entorno con el stack del recolector (curl falla por HTTP/2,
    httpx funciona).
    """

    source = "ANAM"
    url = "https://www.anam.gob.mx/sitemap.rss"
    default_authority = "Agencia Nacional de Aduanas de México"
    default_document_type = "Comunicado"
    # El feed lista también páginas índice/navegación ("Comunicados de Prensa
    # 2026", "Boletines..."): se descartan por título; el filtro de relevancia
    # remata cualquier página institucional sin materia.
    skip_title_re = re.compile(
        r"^(comunicados de prensa|boletines|inicio$|directorio|aviso de privacidad)",
        re.IGNORECASE,
    )
