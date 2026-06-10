from datetime import date

from app.sources.dof import DofCollector
from app.sources.gobmx import GobMxCollector
from app.sources.impi import ImpiCollector
from app.sources.senado import SenadoCollector


def test_dof_parser_extracts_official_item():
    xml = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
    <rss><channel><item>
      <title>PODER EJECUTIVO SECRETARIA DE HACIENDA</title>
      <link>https://dof.gob.mx/nota_detalle.php?codigo=123&amp;fecha=10/06/2026</link>
      <description>
        Resolucion de Modificaciones a las Reglas Generales de Comercio Exterior.
      </description>
      <valueDate>10/06/2026</valueDate>
    </item></channel></rss>"""

    items = DofCollector.parse(xml, date(2026, 6, 1))

    assert len(items) == 1
    assert items[0].source_id == "123"
    assert items[0].published_at == date(2026, 6, 10)


def test_dof_parser_ignores_incomplete_feed_tail():
    xml = b"""<rss><channel><item>
      <title>SECRETARIA DE ECONOMIA</title>
      <link>https://dof.gob.mx/nota_detalle.php?codigo=456</link>
      <description>Acuerdo en materia de comercio exterior.</description>
      <valueDate>10/06/2026</valueDate>
    </item><item><description><![CDATA[texto incompleto"""

    items = DofCollector.parse(xml, date(2026, 6, 1))

    assert [item.source_id for item in items] == ["456"]


def test_senado_parser_uses_public_json():
    payload = {
        "data": [
            {
                "fecha_documento": "01/06/2026",
                "titulo_docuemnto": "Proyecto de decreto que reforma el Código Fiscal.",
                "tema_documento": "Obligaciones de información fiscal.",
                "link_documento": "https://www.senado.gob.mx/documento/1",
                "organo": "Cámara de Diputados",
                "tipo_documento": "Iniciativa de Ley",
                "nota": "",
            }
        ]
    }

    items = SenadoCollector.parse(payload, date(2026, 5, 1))

    assert len(items) == 1
    assert items[0].document_type == "Iniciativa de Ley"


def test_senado_parser_excludes_points_of_agreement():
    payload = {
        "data": [
            {
                "fecha_documento": "01/06/2026",
                "titulo_docuemnto": "Punto de acuerdo sobre recaudación.",
                "tema_documento": "Impuestos",
                "link_documento": "https://www.senado.gob.mx/documento/2",
                "organo": "Senado",
                "tipo_documento": "Punto de Acuerdo",
            }
        ]
    }

    assert SenadoCollector.parse(payload, date(2026, 5, 1)) == []


def test_impi_parser_accepts_link_wrapping_heading():
    payload = """
    <article>
      <a href="/publicaciones/nombramiento"><h3>Nuevo Director General del IMPI</h3></a>
      <time date="2026-06-01 14:24:00">1 de junio de 2026</time>
      <p>Se anunció el nombramiento del nuevo Director General.</p>
    </article>
    """

    items = ImpiCollector.parse(payload, date(2026, 5, 1))

    assert len(items) == 1
    assert items[0].url == "https://www.impi.gob.mx/publicaciones/nombramiento"


def test_gobmx_parser_discovers_institutional_portals_only():
    payload = b"""<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>http://www.gob.mx/sitemaps/sitemap-se.xml</loc></sitemap>
      <sitemap><loc>http://www.gob.mx/sitemaps/sitemap-shcp.xml</loc></sitemap>
      <sitemap><loc>http://www.gob.mx/sitemaps/sitemap-comolehago.xml</loc></sitemap>
    </sitemapindex>"""

    assert GobMxCollector.parse_portals(payload) == ["se", "shcp"]


def test_gobmx_parser_reads_javascript_archive_response():
    payload = r'''
      $("#prensa").append("<article>\n
        <time datetime=\"2026-06-01 14:19:00\">01 de junio de 2026<\/time>\n
        <h2>Vidal Llerenas nuevo titular del IMPI<\/h2>\n
        <a href=\"/se/prensa/vidal-llerenas-nuevo-titular-del-impi-427404\">Leer<\/a>\n
      <\/article>");
    '''

    items = GobMxCollector.parse_archive(payload, "se", "prensa")

    assert len(items) == 1
    assert items[0].published_at == date(2026, 6, 1)
    assert items[0].url.endswith("vidal-llerenas-nuevo-titular-del-impi-427404")
    assert GobMxCollector.looks_relevant(items[0].title)
    assert not GobMxCollector.looks_relevant(
        "CONADE presenta su estrategia de masificación deportiva"
    )
