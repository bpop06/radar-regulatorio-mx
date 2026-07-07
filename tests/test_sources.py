import json
from datetime import date

from app.relevance import classify
from app.sources.diputados import DiputadosCollector
from app.sources.dof import DofCollector
from app.sources.gobmx import ALWAYS_RELEVANT_PORTALS, GobMxCollector
from app.sources.icsid import IcsidCollector
from app.sources.impi import ImpiCollector
from app.sources.international import (
    CijCollector,
    CpiCollector,
    OnuNoticiasCollector,
    TradeGovCollector,
    UstrCollector,
)
from app.sources.senado import SenadoCollector
from app.sources.worldbank import WorldBankCollector
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


WORDPRESS_NAMESPACED_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:content="http://purl.org/rss/1.0/modules/content/"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Tradeology</title>
    <item>
      <title>ITA announces new tariff guidance for USMCA partners</title>
      <link>https://blog.trade.gov/2026/07/01/tariff-guidance/</link>
      <dc:creator><![CDATA[ITA Press]]></dc:creator>
      <pubDate>Wed, 01 Jul 2026 15:00:00 +0000</pubDate>
      <guid isPermaLink="false">https://blog.trade.gov/?p=12345</guid>
      <description><![CDATA[Guidance on <b>tariff</b> treatment.]]></description>
      <content:encoded><![CDATA[<p>Full post body with markup.</p>]]></content:encoded>
    </item>
  </channel>
</rss>
"""


def test_rss_parser_survives_wordpress_namespaced_items():
    # Los feeds WordPress/Drupal (Trade.gov, USTR) traen hijos con prefijo de
    # namespace declarado en la raíz; el bloque <item> aislado debe parsearse
    # igual en el reintento sin prefijos.
    items = TradeGovCollector.parse(WORDPRESS_NAMESPACED_RSS, date(2026, 6, 1))

    assert len(items) == 1
    assert items[0].official_title == "ITA announces new tariff guidance for USMCA partners"
    assert items[0].url == "https://blog.trade.gov/2026/07/01/tariff-guidance/"
    assert items[0].description == "Guidance on tariff treatment."


# --- CPI (ICC) y CIJ (ICJ) — fase 4, segunda ola --------------------------
#
# Ambos feeds fueron confirmados en vivo (2026-07-07): RSS 2.0 estándar con
# pubDate/guid, description con HTML escapado (no CDATA) — igual que ONU/
# USTR/Trade.gov, comparten el parser tolerante de RssCollector sin cambios.
# Los fragmentos de abajo están recortados de una corrida real de
# https://www.icc-cpi.int/rss.xml y https://www.icj-cij.org/rss.xml.

ICC_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>International Criminal Court</title>
<item>
  <title>ICC hosts roundtable on State cooperation on release and interim release</title>
  <link>https://www.icc-cpi.int/news/icc-hosts-roundtable-state-cooperation-release-and-interim-release</link>
  <description>&lt;span class="field field--name-title"&gt;ICC hosts roundtable on State
  cooperation on release and interim release&lt;/span&gt;
  &lt;div class="field field--name-field-document-type"&gt;Press Release&lt;/div&gt;</description>
  <guid isPermaLink="false">238352 at https://www.icc-cpi.int</guid>
  <pubDate>Mon, 06 Jul 2026 14:24:09 +0000</pubDate>
</item>
<item>
  <title>ICC Prosecutor concludes visit on war crimes cooperation</title>
  <link>https://www.icc-cpi.int/news/icc-prosecutor-concludes-visit-2019</link>
  <description>&lt;span&gt;Historical press release from 2019.&lt;/span&gt;</description>
  <guid isPermaLink="false">19001 at https://www.icc-cpi.int</guid>
  <pubDate>Fri, 15 Mar 2019 10:00:00 +0000</pubDate>
</item>
<item>
  <title>Broken ICC item</title>
  <link>https://www.icc-cpi.int/news/broken</link>
  <description>Text with a stray & that breaks the XML</description>
  <pubDate>Mon, 06 Jul 2026 08:00:00 +0000</pubDate>
</item>
</channel></rss>"""

ICJ_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>INTERNATIONAL COURT OF JUSTICE</title>
<item>
  <title>20260630-200-InterventionPoland</title>
  <link>https://www.icj-cij.org/node/206439</link>
  <description>20260630-200-InterventionPoland uploaded media node</description>
  <guid isPermaLink="false">206439 at https://www.icj-cij.org</guid>
  <pubDate>Tue, 30 Jun 2026 15:23:40 +0000</pubDate>
</item>
<item>
  <title>Poland files a declaration of intervention under Article 63</title>
  <link>https://www.icj-cij.org/node/206438</link>
  <description>&lt;p&gt;On 30 June 2026, Poland filed a declaration of intervention under
  Article 63 of the Statute of the Court in the case concerning Alleged Smuggling of
  Migrants (Lithuania v. Belarus).&lt;/p&gt;</description>
  <guid isPermaLink="false">206438 at https://www.icj-cij.org</guid>
  <pubDate>Tue, 30 Jun 2026 15:23:38 +0000</pubDate>
</item>
<item>
  <title>Historical judgment summary from 2018</title>
  <link>https://www.icj-cij.org/node/100000</link>
  <description>&lt;p&gt;Summary of a judgment rendered in 2018.&lt;/p&gt;</description>
  <guid isPermaLink="false">100000 at https://www.icj-cij.org</guid>
  <pubDate>Wed, 10 Jan 2018 09:00:00 +0000</pubDate>
</item>
<item>
  <title>Broken ICJ item</title>
  <link>https://www.icj-cij.org/node/broken</link>
  <description>Text with a stray & that breaks the XML</description>
  <pubDate>Tue, 30 Jun 2026 08:00:00 +0000</pubDate>
</item>
</channel></rss>"""


def test_cpi_parser_extracts_candidate_and_filters_by_since():
    items = CpiCollector.parse(ICC_RSS, date(2026, 6, 1))

    assert len(items) == 1
    item = items[0]
    assert item.source == "CPI"
    assert item.authority == "Corte Penal Internacional"
    assert item.document_type == "Comunicado"
    assert item.official_title == (
        "ICC hosts roundtable on State cooperation on release and interim release"
    )
    assert item.published_at == date(2026, 7, 6)
    assert "<" not in item.description
    assert "Press Release" in item.description


def test_cij_parser_extracts_candidate_and_filters_by_since():
    items = CijCollector.parse(ICJ_RSS, date(2026, 6, 1))

    assert len(items) == 1
    item = items[0]
    assert item.source == "CIJ"
    assert item.authority == "Corte Internacional de Justicia"
    assert item.official_title == (
        "Poland files a declaration of intervention under Article 63"
    )
    assert item.published_at == date(2026, 6, 30)
    assert "<" not in item.description


# --- Diputados: anexos de la Gaceta Parlamentaria -------------------------
#
# Fragmento recortado pero fiel de una corrida real de
# https://gaceta.diputados.gob.mx/gp_hoy.html (confirmada en vivo el
# 2026-07-07, que ese día seguía mostrando la edición del lunes 6 de julio
# de 2026 por no haber sesión nueva todavía): el div `#Anexos` trae enlaces
# "Anexo <N>" a PDFs con el patrón /PDF/<legislatura>/AAAA/mes/AAAAMMDD-*.pdf
# seguidos, en el mismo <p>, de una descripción breve de su contenido.

GACETA_CON_ANEXOS = """
<html><head>
<title>Gaceta Parlamentaria, año XXIX, número 7075, lunes 6 de julio de 2026</title>
</head><body>
<div id="Anexos">
<p><a href="/PDF/66/2026/jul/20260706-I.pdf" target="gaceta">Anexo I</a>
Iniciativas recibidas en las sesiones de la Comisión Permanente
</p>
<p><a href="/PDF/66/2026/jul/20260706-II.pdf" target="gaceta">Anexo II</a>
Comunicaciones de la Junta de Coordinación Política
</p>
</div>
<div id="Indice">
<a class="Seccion" href="#Iniciativas">Iniciativas</a>
<ul>
<li>
<a class="Indice" href="#Iniciativa1">
Que reforma diversas disposiciones de la Ley del Impuesto al Valor Agregado
</a>
</li>
</ul>
</div>
</body></html>
""".encode("iso-8859-1")


def test_diputados_parser_extracts_anexos_alongside_index():
    items = DiputadosCollector.parse(GACETA_CON_ANEXOS, date(2026, 6, 1))

    anexos = [item for item in items if item.document_type == "Dictámenes y minutas"]
    assert len(anexos) == 2

    first = anexos[0]
    assert first.official_title == (
        "Anexo I de la Gaceta Parlamentaria (dictámenes y minutas del Pleno)"
    )
    assert first.published_at == date(2026, 7, 6)
    assert first.url == "https://gaceta.diputados.gob.mx/PDF/66/2026/jul/20260706-I.pdf"
    assert first.authority == "Cámara de Diputados"
    assert "Iniciativas recibidas" in first.description

    # El índice original sigue funcionando junto a los anexos.
    index_items = [item for item in items if item.document_type != "Dictámenes y minutas"]
    assert len(index_items) == 1


def test_diputados_parser_filters_anexos_by_their_own_filename_date():
    # since posterior a la fecha de ambos anexos (tomada del nombre de
    # archivo, no del título de la gaceta): deben quedar excluidos aunque la
    # gaceta en sí siga vigente.
    items = DiputadosCollector.parse(GACETA_CON_ANEXOS, date(2026, 7, 7))

    anexos = [item for item in items if item.document_type == "Dictámenes y minutas"]
    assert anexos == []


# --- Gob.mx: portales siempre relevantes ----------------------------------


def test_prodecon_is_always_relevant_despite_generic_titles():
    # Los boletines de PRODECON tienen títulos genéricos (números de
    # boletín) que nunca pasan el filtro de relevancia por título, aunque su
    # mandato entero (defensa del contribuyente) es fiscal.
    generic_title = "Boletín 09/ 2026"
    assert not GobMxCollector.looks_relevant(generic_title)
    assert "prodecon" in ALWAYS_RELEVANT_PORTALS


def test_condusef_and_profeco_are_not_always_relevant():
    # Verificado en vivo: sus boletines de prensa son mayormente protección
    # al consumidor genérica (alertas de suplantación de identidad, precios
    # de alimentos, quejas de aerolíneas), ajena al alcance del radar;
    # incluirlos sin filtro inundaría el radar con ruido.
    assert "condusef" not in ALWAYS_RELEVANT_PORTALS
    assert "profeco" not in ALWAYS_RELEVANT_PORTALS


# --- Banco Mundial ---------------------------------------------------------
#
# Estructura recortada pero fiel de una respuesta real de
# https://search.worldbank.org/api/v2/news (confirmada en vivo el
# 2026-07-07): `documents` es un diccionario keyed por id que mezcla, junto a
# los documentos reales, una entrada de metadatos "facets" sin título/url.

WB_PAYLOAD = {
    "rows": 3,
    "total": 1851,
    "documents": {
        "facets": {},
        "doc-1": {
            "id": "doc-1",
            "url": (
                "http://www.bancomundial.org/es/news/press-release/2026/07/01/"
                "banco-mundial-amplia-funciones"
            ),
            "title": {
                "cdata!": (
                    "Banco Mundial amplía funciones de Juan Pablo Uribe como "
                    "Director de División para México"
                )
            },
            "descr": {
                "cdata!": (
                    "El Banco Mundial anunció la ampliación de las "
                    "responsabilidades de Juan Pablo Uribe para México."
                )
            },
            "lnchdt": "2026-07-01T18:04:00Z",
            "country": "Mexico",
            "conttype": "Press Release",
        },
        "doc-2": {
            "id": "doc-2",
            "url": "http://www.bancomundial.org/es/news/press-release/2019/01/01/historico",
            "title": {"cdata!": "Comunicado histórico de 2019"},
            "descr": {"cdata!": "Texto histórico."},
            "lnchdt": "2019-01-01T12:00:00Z",
            "country": "Mexico",
            "conttype": "Press Release",
        },
    },
}


def test_worldbank_parser_extracts_candidate_and_skips_facets_entry():
    items = WorldBankCollector.parse(WB_PAYLOAD, date(2026, 6, 1))

    assert len(items) == 1
    item = items[0]
    assert item.source == "Banco Mundial"
    assert item.published_at == date(2026, 7, 1)
    assert "Juan Pablo Uribe" in item.official_title
    assert item.url.startswith("http://www.bancomundial.org/")
    assert "Uribe" in item.description


def test_worldbank_parser_filters_by_since():
    items = WorldBankCollector.parse(WB_PAYLOAD, date(2026, 1, 1))
    urls = [item.url for item in items]
    assert not any("2019" in url for url in urls)


# --- CIADI (ICSID) ----------------------------------------------------------
#
# Estructura recortada pero fiel de una respuesta real de
# https://icsid.worldbank.org/api/all/cases (confirmada en vivo el
# 2026-07-07: 1149 casos totales, 56 de México). `subject`/`econsector`
# llegan vacíos para todos los casos en ese endpoint, no solo los de México.

def _icsid_case(caseno: str, claimant: str, respondent: str, status: str) -> dict:
    return {
        "caseno": caseno,
        "claimant": claimant,
        "respondent": respondent,
        "status": status,
        "subject": "",
        "econsector": "",
    }


ICSID_PAYLOAD = {
    "data": {
        "GetAllCasesResult": [
            _icsid_case(
                "ARB/25/7", "Draslovka Holding A.S.", "United Mexican States", "Pending"
            ),
            _icsid_case("ARB/25/32", "Allianz SE", "United Mexican States", "Concluded"),
            _icsid_case(
                "ARB/26/31",
                "Galp Energia, SGPS, S.A.",
                "Republic of Mozambique",
                "Pending",
            ),
        ]
    },
    "method": "GetAllCases",
}


def test_icsid_parser_filters_non_mexico_cases():
    items = IcsidCollector.parse(
        ICSID_PAYLOAD,
        snapshot_path="/nonexistent/snapshot-should-not-be-read.json",
        today=date(2026, 7, 7),
    )
    # El caso de Mozambique nunca debe aparecer, ni en la primera corrida.
    assert all("Mozambique" not in item.official_title for item in items)


def test_icsid_first_run_emits_only_pending_and_saves_full_snapshot(tmp_path):
    snapshot_path = tmp_path / "icsid_snapshot.json"
    assert not snapshot_path.exists()

    items = IcsidCollector.parse(
        ICSID_PAYLOAD, snapshot_path=snapshot_path, today=date(2026, 7, 7)
    )

    # Solo el caso Pending de México se emite en la primera corrida (el
    # Concluded no genera ruido histórico).
    assert len(items) == 1
    item = items[0]
    assert item.source == "CIADI"
    assert item.case_status == "Pending"
    assert item.case_parties == "Draslovka Holding A.S. v. United Mexican States"
    assert item.official_title == (
        "Draslovka Holding A.S. v. United Mexican States (Caso CIADI No. ARB/25/7)"
    )
    assert item.url == (
        "https://icsid.worldbank.org/cases/case-database/case-detail?CaseNo=ARB/25/7"
    )
    assert item.published_at == date(2026, 7, 7)
    assert item.authority == (
        "Centro Internacional de Arreglo de Diferencias Relativas a Inversiones"
    )

    # El snapshot completo (Pending + Concluded de México) queda guardado
    # como línea base, aunque el Concluded no haya generado Candidate.
    saved = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert saved == {"ARB/25/7": "Pending", "ARB/25/32": "Concluded"}


def test_icsid_dry_run_does_not_consume_novelty(tmp_path):
    snapshot_path = tmp_path / "icsid_snapshot.json"

    # Una corrida sin persistencia (collect --dry-run) emite los candidatos
    # pero NO guarda el snapshot: la novedad sigue disponible para la
    # siguiente corrida real.
    items = IcsidCollector.parse(
        ICSID_PAYLOAD,
        snapshot_path=snapshot_path,
        today=date(2026, 7, 7),
        persist_snapshot=False,
    )

    assert len(items) == 1
    assert not snapshot_path.exists()


def test_icsid_next_run_emits_only_new_or_changed_cases(tmp_path):
    snapshot_path = tmp_path / "icsid_snapshot.json"
    snapshot_path.write_text(
        json.dumps({"ARB/25/7": "Pending", "ARB/25/32": "Concluded"}),
        encoding="utf-8",
    )

    changed_payload = {
        "data": {
            "GetAllCasesResult": [
                # Sin cambios: no debe emitirse.
                _icsid_case(
                    "ARB/25/7", "Draslovka Holding A.S.", "United Mexican States", "Pending"
                ),
                # Cambio de estatus: debe emitirse.
                _icsid_case("ARB/25/32", "Allianz SE", "United Mexican States", "Concluded"),
                # Caso nuevo, ausente del snapshot anterior: debe emitirse.
                _icsid_case(
                    "ARB/26/99", "Nueva Demandante S.A.", "United Mexican States", "Pending"
                ),
            ]
        },
        "method": "GetAllCases",
    }
    # Simula el cambio real: ARB/25/32 pasó de Concluded a "Annulment
    # proceeding" para probar que cualquier cambio de estatus se detecta,
    # no solo Pending->Concluded.
    changed_payload["data"]["GetAllCasesResult"][1]["status"] = "Annulment proceeding"

    items = IcsidCollector.parse(
        changed_payload, snapshot_path=snapshot_path, today=date(2026, 7, 8)
    )

    emitted_casenos = {
        item.url.rsplit("CaseNo=", 1)[1] for item in items
    }
    assert emitted_casenos == {"ARB/25/32", "ARB/26/99"}
    for item in items:
        assert item.published_at == date(2026, 7, 8)

    saved = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert saved == {
        "ARB/25/7": "Pending",
        "ARB/25/32": "Annulment proceeding",
        "ARB/26/99": "Pending",
    }


def test_icsid_snapshot_path_configurable_via_env(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "env_snapshot.json"
    monkeypatch.setenv("RADAR_ICSID_SNAPSHOT", str(snapshot_path))

    collector = IcsidCollector(client=None)

    assert collector.snapshot_path == snapshot_path
