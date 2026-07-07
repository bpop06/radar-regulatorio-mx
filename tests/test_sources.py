from datetime import date

from app.relevance import classify
from app.sources.dof import DofCollector
from app.sources.gobmx import GobMxCollector
from app.sources.impi import ImpiCollector
from app.sources.international import (
    OnuNoticiasCollector,
    TradeGovCollector,
    UstrCollector,
)
from app.sources.senado import SenadoCollector
from app.taxonomy import enrich


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


# --- Fuentes internacionales (fase 4, primera ola) ---------------------------
#
# Los tres recolectores comparten el parser tolerante de app/sources/international.py
# (bloques <item> aislados por regex + ElementTree.fromstring con try/except,
# igual que app/sources/dof.py). Cada fixture incluye: un ítem vigente con HTML
# en <description>, un ítem viejo para probar el filtro `since`, y un bloque
# malformado (un "&" suelto rompe el XML) que debe descartarse sin tumbar el feed.

ONU_NOTICIAS_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Noticias ONU</title>
<item>
  <title>Mexico y la ONU refuerzan cooperacion en comercio internacional</title>
  <link>https://news.un.org/es/story/2026/06/1234567</link>
  <guid isPermaLink="false">https://news.un.org/es/story/2026/06/1234567</guid>
  <description><![CDATA[<p>La Organizacion de las Naciones Unidas <b>anuncio</b> nuevas medidas
  de cooperacion comercial con Mexico y otros paises de la region.</p>]]></description>
  <pubDate>Mon, 08 Jun 2026 12:00:00 GMT</pubDate>
</item>
<item>
  <title>Informe historico sobre comercio internacional</title>
  <link>https://news.un.org/es/story/2024/01/7654321</link>
  <guid isPermaLink="false">https://news.un.org/es/story/2024/01/7654321</guid>
  <description>Informe presentado en enero de 2024 sobre tendencias historicas.</description>
  <pubDate>Wed, 10 Jan 2024 09:00:00 GMT</pubDate>
</item>
<item>
  <title>Comunicado con formato roto</title>
  <link>https://news.un.org/es/story/2026/06/000000</link>
  <description>Texto con un & suelto que rompe el XML</description>
  <pubDate>Mon, 08 Jun 2026 08:00:00 GMT</pubDate>
</item>
</channel></rss>"""

USTR_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>USTR Press Releases</title>
<item>
  <title>USTR Announces Section 301 Findings on Forced Labor Goods</title>
  <link>https://ustr.gov/about/policy-offices/press-office/press-releases/2026/june/findings</link>
  <guid isPermaLink="true">https://ustr.gov/about/policy-offices/press-office/press-releases/2026/june/findings</guid>
  <description><![CDATA[<p>The Office of the United States Trade Representative announced
  <b>Section 301</b> findings and a tariff review tied to the ongoing USMCA joint review with
  Mexico and Canada.</p>]]></description>
  <pubDate>Tue, 09 Jun 2026 15:00:00 -0400</pubDate>
</item>
<item>
  <title>USTR 2019 Report on Technical Barriers to Trade</title>
  <link>https://ustr.gov/archive/2019/report-tbt</link>
  <guid isPermaLink="true">https://ustr.gov/archive/2019/report-tbt</guid>
  <description>Annual legacy report from 2019.</description>
  <pubDate>Fri, 15 Mar 2019 10:00:00 -0400</pubDate>
</item>
<item>
  <title>Broken press release</title>
  <link>https://ustr.gov/about/policy-offices/press-office/press-releases/2026/june/broken</link>
  <description>Text with a stray & that breaks the XML</description>
  <pubDate>Mon, 08 Jun 2026 08:00:00 GMT</pubDate>
</item>
</channel></rss>"""

TRADE_GOV_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Tradeology - International Trade Administration Blog</title>
<item>
  <title>ITA Supports US-Mexico Trade Relationship with New Export Guidance</title>
  <link>https://blog.trade.gov/2026/06/09/ita-supports-us-mexico-trade-relationship/</link>
  <guid isPermaLink="true">https://blog.trade.gov/?p=98765</guid>
  <description><![CDATA[<p>The International Trade Administration published <em>new guidance</em>
  on customs duties and export control requirements affecting the US-Mexico trade
  relationship.</p>]]></description>
  <pubDate>Tue, 09 Jun 2026 13:00:00 -0400</pubDate>
</item>
<item>
  <title>Archived 2018 Export Success Story</title>
  <link>https://blog.trade.gov/2018/02/10/archived-story/</link>
  <guid isPermaLink="true">https://blog.trade.gov/?p=11111</guid>
  <description>Historical export success story from 2018.</description>
  <pubDate>Sat, 10 Feb 2018 09:00:00 -0500</pubDate>
</item>
<item>
  <title>Broken blog post</title>
  <link>https://blog.trade.gov/2026/06/broken/</link>
  <description>Text with a stray & that breaks the XML</description>
  <pubDate>Mon, 08 Jun 2026 08:00:00 GMT</pubDate>
</item>
</channel></rss>"""


def test_onu_noticias_parser_extracts_candidate_and_cleans_html():
    items = OnuNoticiasCollector.parse(ONU_NOTICIAS_RSS, date(2026, 6, 1))

    assert len(items) == 1
    item = items[0]
    assert item.source == "ONU Noticias"
    assert item.official_title == (
        "Mexico y la ONU refuerzan cooperacion en comercio internacional"
    )
    assert item.url == "https://news.un.org/es/story/2026/06/1234567"
    assert item.published_at == date(2026, 6, 8)
    assert "<" not in item.description
    assert "CDATA" not in item.description
    assert "anuncio" in item.description


def test_onu_noticias_parser_filters_by_since_and_skips_malformed_item():
    items = OnuNoticiasCollector.parse(ONU_NOTICIAS_RSS, date(2026, 6, 1))

    # El item de 2024 (anterior a `since`) y el bloque con un "&" suelto,
    # que rompe el parseo XML, deben quedar fuera sin afectar al resto.
    urls = [item.url for item in items]
    assert "https://news.un.org/es/story/2024/01/7654321" not in urls
    assert "https://news.un.org/es/story/2026/06/000000" not in urls


def test_ustr_parser_extracts_candidate_and_filters_by_since():
    items = UstrCollector.parse(USTR_RSS, date(2026, 6, 1))

    assert len(items) == 1
    item = items[0]
    assert item.source == "USTR"
    assert item.official_title == "USTR Announces Section 301 Findings on Forced Labor Goods"
    assert item.published_at == date(2026, 6, 9)
    assert "<" not in item.description
    assert "Section 301" in item.description


def test_trade_gov_parser_extracts_candidate_and_filters_by_since():
    items = TradeGovCollector.parse(TRADE_GOV_RSS, date(2026, 6, 1))

    assert len(items) == 1
    item = items[0]
    assert item.source == "Trade.gov"
    assert item.official_title == (
        "ITA Supports US-Mexico Trade Relationship with New Export Guidance"
    )
    assert item.published_at == date(2026, 6, 9)
    assert "<" not in item.description


def test_international_parser_discards_items_without_title_or_http_link():
    payload = b"""<?xml version="1.0"?>
    <rss><channel>
      <item>
        <title></title>
        <link>https://example.test/sin-titulo</link>
        <pubDate>Mon, 08 Jun 2026 08:00:00 GMT</pubDate>
      </item>
      <item>
        <title>Enlace no http</title>
        <link>ftp://example.test/no-http</link>
        <pubDate>Mon, 08 Jun 2026 08:00:00 GMT</pubDate>
      </item>
    </channel></rss>"""

    assert OnuNoticiasCollector.parse(payload, date(2026, 6, 1)) == []


def test_ustr_candidate_classifies_as_international_taxonomy():
    # Integracion recolector -> clasificacion -> taxonomia: un comunicado real
    # de USTR debe terminar marcado como jurisdiccion internacional / EEUU.
    items = UstrCollector.parse(USTR_RSS, date(2026, 6, 1))
    assert len(items) == 1

    classified = classify(items[0])
    taxonomy = enrich(classified)

    assert taxonomy.jurisdiction == "internacional"
    assert taxonomy.country_or_org == "EEUU"
