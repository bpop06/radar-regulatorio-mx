import asyncio
import json
from datetime import date

import httpx
import pytest

from app.models import Candidate, SourceResult
from app.pipeline import _collect_results, _collect_source, _deduplicate, _dof_previous_day_reviewed
from app.relevance import classify
from app.sources.base import Collector, SourceContractError
from app.sources.certification import RELAUNCH_SOURCES, certification_report
from app.sources.diputados import DiputadosCollector, permanent_gaceta_url
from app.sources.dof import DofCollector
from app.sources.gobmx import (
    ALWAYS_RELEVANT_PORTALS,
    GobMxCollector,
    canonical_gobmx_url,
    gobmx_source_id,
    sanitize_gobmx_title,
)
from app.sources.icsid import IcsidCollector, parse_case_detail
from app.sources.impi import ImpiCollector
from app.sources.international import (
    CijCollector,
    CpiCollector,
    OmcCollector,
    OnuNoticiasCollector,
    TradeGovCollector,
    UstrCollector,
    extract_international_case,
)
from app.sources.platiica import PlatiicaCollector
from app.sources.senado import SenadoCollector
from app.sources.snice import SniceCollector
from app.sources.worldbank import WorldBankCollector, canonical_worldbank_url
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


def test_dof_parser_cleans_document_type_of_act_numbers_and_trailing_connectors():
    # Regresión #5: tomar las primeras palabras a secas del sumario del DOF
    # arrastraba números de oficio/acuerdo y conectores sueltos ("Acuerdo Por
    # El", "Oficio 500-05-00-00-00-2026-16021 Mediante").
    xml = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
    <rss><channel>
    <item>
      <title>PODER EJECUTIVO SECRETARIA DE ECONOMIA</title>
      <link>https://dof.gob.mx/nota_detalle.php?codigo=111&amp;fecha=10/06/2026</link>
      <description>Acuerdo por el que se delegan facultades a la unidad.</description>
      <valueDate>10/06/2026</valueDate>
    </item>
    <item>
      <title>PODER EJECUTIVO SECRETARIA DE HACIENDA</title>
      <link>https://dof.gob.mx/nota_detalle.php?codigo=222&amp;fecha=10/06/2026</link>
      <description>
        Oficio 500-05-00-00-00-2026-16021 mediante el cual se comunica el listado.
      </description>
      <valueDate>10/06/2026</valueDate>
    </item>
    </channel></rss>"""

    items = DofCollector.parse(xml, date(2026, 6, 1))

    by_id = {item.source_id: item for item in items}
    assert by_id["111"].document_type == "Acuerdo"
    assert by_id["222"].document_type == "Oficio"


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


def test_senado_parser_rejects_external_document_link():
    payload = {
        "data": [
            {
                "fecha_documento": "01/06/2026",
                "titulo_docuemnto": "Proyecto de decreto fiscal.",
                "tema_documento": "Impuestos",
                "link_documento": "https://evil.example/documento/1",
                "organo": "Senado",
                "tipo_documento": "Iniciativa de Ley",
            }
        ]
    }

    assert SenadoCollector.parse(payload, date(2026, 5, 1)) == []


def test_platiica_parser_accepts_valid_empty_list_and_stable_post_id():
    assert PlatiicaCollector.parse([], date(2026, 8, 1)) == []

    items = PlatiicaCollector.parse(
        [
            {
                "id": 987,
                "modified": "2026-08-12T13:30:00",
                "link": "https://platiica.economia.gob.mx/consulta-publica/nmx-1/",
                "slug": "consulta-publica-nmx-1",
                "title": {"rendered": "Consulta pública de proyecto de norma mexicana"},
                "excerpt": {"rendered": "<p>Proyecto en consulta por 60 días.</p>"},
            }
        ],
        date(2026, 8, 1),
    )
    assert len(items) == 1
    assert items[0].source_id == "987"
    assert items[0].url.endswith("/consulta-publica/nmx-1/")


def test_snice_parser_uses_official_actualidad_block():
    payload = """
    <html><body><!-- ACTUALIDAD -->
    <section id="actualidad"><p><a href="/~oracle/SNICE_DOCS/aviso.pdf">
    12.08.2026 Aviso sobre cupos de importación y compromisos arancelarios del TIPAT
    </a></p></section></body></html>
    """
    items = SniceCollector.parse(payload, date(2026, 8, 1))
    assert len(items) == 1
    assert items[0].published_at == date(2026, 8, 12)
    assert items[0].url == "https://www.snice.gob.mx/~oracle/SNICE_DOCS/aviso.pdf"


@pytest.mark.parametrize(
    "href",
    [
        "https://evil.example/act.pdf",
        "//evil.example/act.pdf",
        "https://usuario@www.snice.gob.mx/act.pdf",
    ],
)
def test_snice_parser_rejects_off_domain_or_credentialed_links(href: str) -> None:
    payload = f"""
    <html><body><!-- ACTUALIDAD -->
    <section id=\"actualidad\"><p><a href=\"{href}\">
    12.08.2026 Aviso sobre cupos de importación y compromisos arancelarios del TIPAT
    </a></p></section></body></html>
    """

    assert SniceCollector.parse(payload, date(2026, 8, 1)) == []


def test_snice_parser_ignores_empty_projects_status_and_keeps_documents():
    payload = """
    <h1>ACTUALIDAD</h1>
    <section>
      <h5>
        <a href="https://www.herramientasregulatorias.gob.mx/Buscador">
          <strong>12.08.2026 Sin proyectos en materia de Comercio Exterior .</strong>
        </a>
        <a href="/cs/avi/snice/historicos.conamer.html">
          (Consulta aquí el histórico de ATDT)
        </a>
      </h5>
      <h5>
        <a href="/~oracle/SNICE_DOCS/AVISO-CUPOS_20260811.pdf">
          <strong>11.08.2026 Aviso sobre los cupos de importación vigentes.</strong>
        </a>
      </h5>
      <h5>
        <a href="/~oracle/SNICE_DOCS/INFORME-PROYECTOS_20260810.pdf">
          <strong>
            10.08.2026 Informe sobre localidades sin proyectos en materia de Comercio Exterior.
          </strong>
        </a>
      </h5>
    </section>
    """

    items = SniceCollector.parse(payload, date(2026, 8, 1))

    assert [item.official_title for item in items] == [
        "Aviso sobre los cupos de importación vigentes.",
        "Informe sobre localidades sin proyectos en materia de Comercio Exterior.",
    ]
    assert all(
        item.url != "https://www.herramientasregulatorias.gob.mx/Buscador"
        for item in items
    )


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
    assert items[0].url == "https://www.gob.mx/publicaciones/nombramiento"


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


def test_gobmx_parser_collapses_exact_title_duplicated_by_archive_markup():
    duplicated = (
        "¿Eres una persona pensionada o jubilada? PRODECON te informa sobre"
        "¿Eres una persona pensionada o jubilada? PRODECON te informa sobre?"
    )
    payload = rf'''
      $("#articulos").append("<article>
        <time datetime=\"2026-06-18 14:19:00\">18 de junio de 2026<\/time>
        <h2>{duplicated}<\/h2>
        <a href=\"/prodecon/es/articulos/titulo-repetido-titulo-repetido?idiom=es\">
          Leer<\/a>
      <\/article>");
    '''

    items = GobMxCollector.parse_archive(payload, "prodecon", "articulos")

    assert len(items) == 1
    assert items[0].title == (
        "¿Eres una persona pensionada o jubilada? PRODECON te informa sobre?"
    )
    assert sanitize_gobmx_title("Una frase legítima sin duplicación") == (
        "Una frase legítima sin duplicación"
    )


def test_gobmx_identity_uses_canonical_url_not_tracking_variants():
    canonical = "https://www.gob.mx/se/prensa/publicacion-oficial"
    variants = (
        canonical,
        "HTTPS://WWW.GOB.MX/se/prensa/publicacion-oficial/",
        f"{canonical}?utm_source=boletin#contenido",
    )

    assert {canonical_gobmx_url(url) for url in variants} == {canonical}
    assert len({gobmx_source_id(url) for url in variants}) == 1

    candidates = [
        Candidate(
            source="Gob.mx APF",
            source_id=gobmx_source_id(url),
            url=url,
            canonical_url=canonical_gobmx_url(url),
            official_title="Publicación oficial",
            description="Descripción " + ("ampliada" if index else "breve"),
            published_at=date(2026, 8, 12),
        )
        for index, url in enumerate(variants[:2])
    ]
    deduplicated = _deduplicate(candidates)
    assert len(deduplicated) == 1
    assert deduplicated[0].description.endswith("ampliada")


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


def test_omc_source_specific_rss_fixture_preserves_official_link_and_date():
    payload = b"""<rss version="2.0"><channel><item>
    <title>WTO members discuss customs valuation and trade facilitation</title>
    <link>https://www.wto.org/english/news_e/news26_e/cus_12aug26_e.htm</link>
    <guid>wto-cus-12aug26</guid>
    <description>Members reviewed customs valuation notifications.</description>
    <pubDate>Wed, 12 Aug 2026 16:00:00 GMT</pubDate>
    </item></channel></rss>"""

    items = OmcCollector.parse(payload, date(2026, 8, 1))
    assert len(items) == 1
    assert items[0].source == "OMC"
    assert items[0].published_at == date(2026, 8, 12)
    assert items[0].url.endswith("cus_12aug26_e.htm")


TRADE_GOV_HTML = """
<html><body><div class="field-body">
<h2>Press Releases Issued by the International Trade Administration</h2>
<h4>July 2026</h4><ul>
<li>7/31/26<br><a href="/press-release/duty-evasion-filing">
Commerce Department Secures Win Following First-Time Duty Evasion Filing</a></li>
<li>7/21/26<br><a href="/feature-article/remarks-at-farnborough">
Remarks by Under Secretary at Farnborough</a></li>
</ul>
<h4>May 2024</h4><ul><li>5/1/24<br>
<a href="/press-release/historical">Historical release</a></li></ul>
</div></body></html>
"""


def test_trade_gov_html_parser_extracts_candidates_and_filters_by_since():
    items = TradeGovCollector.parse(TRADE_GOV_HTML, date(2026, 6, 1))

    assert len(items) == 2
    item = items[0]
    assert item.source == "Trade.gov"
    assert item.official_title == (
        "Commerce Department Secures Win Following First-Time Duty Evasion Filing"
    )
    assert item.published_at == date(2026, 7, 31)
    assert item.url == "https://www.trade.gov/press-release/duty-evasion-filing"


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
    # Los feeds WordPress/Drupal (como USTR) traen hijos con prefijo de
    # namespace declarado en la raíz; el bloque <item> aislado debe parsearse
    # igual en el reintento sin prefijos.
    items = UstrCollector.parse(WORDPRESS_NAMESPACED_RSS, date(2026, 6, 1))

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
    assert not item.case_number
    assert not item.case_parties
    assert not item.case_claim


ICC_CASE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>International Criminal Court</title>
<item>
  <title>Trial Chamber IX issues reparations order in the Ongwen case</title>
  <link>https://www.icc-cpi.int/news/ongwen-reparations-order</link>
  <description>&lt;p&gt;The case The Prosecutor v. Dominic Ongwen (ICC-02/04-01/15)
  concerns war crimes and crimes against humanity. Trial Chamber IX orders
  reparations of EUR 52.4 million. The order is final.&lt;/p&gt;</description>
  <guid isPermaLink="false">ongwen-reparations-order</guid>
  <pubDate>Mon, 06 Jul 2026 14:24:09 +0000</pubDate>
</item>
</channel></rss>"""


def test_cpi_parser_extracts_only_explicit_case_metadata_from_official_description():
    item = CpiCollector.parse(ICC_CASE_RSS, date(2026, 6, 1))[0]

    assert item.case_number == "ICC-02/04-01/15"
    assert item.case_parties == "The Prosecutor v. Dominic Ongwen"
    assert item.case_claim == "war crimes and crimes against humanity"
    assert item.case_status == "The order is final."
    assert item.case_outcome == "Trial Chamber IX orders reparations of EUR 52.4 million."
    assert item.case_amount == "EUR 52.4 million"
    assert item.official_evidence == {
        "case_number": "ICC-02/04-01/15",
        "case_parties": "The Prosecutor v. Dominic Ongwen",
        "litis": (
            "The case The Prosecutor v. Dominic Ongwen (ICC-02/04-01/15) "
            "concerns war crimes and crimes against humanity."
        ),
        "status": "The order is final.",
        "outcome": "Trial Chamber IX orders reparations of EUR 52.4 million.",
        "amount": "EUR 52.4 million",
    }


def test_cpi_extractor_reads_al_mahdi_case_only_from_literal_official_text():
    description = (
        "The Trust Fund has completed the implementation of the reparations programme "
        "in the case of The Prosecutor v. Ahmad Al Faqi Al Mahdi. "
        "Mr Al Mahdi was convicted for the war crime of intentionally directing attacks "
        "against historic and religious monuments in Timbuktu in 2012. "
        "ICC judges instructed him to pay EUR 2.7 million for reparations to the victims."
    )

    case = extract_international_case(
        "CPI",
        "ICC Trust Fund for Victims Completes Reparations Programme in Mali",
        description,
        "https://www.icc-cpi.int/news/al-mahdi-reparations",
    )

    assert not case.number
    assert case.parties == "The Prosecutor v. Ahmad Al Faqi Al Mahdi"
    assert case.claim == (
        "the war crime of intentionally directing attacks against historic and religious "
        "monuments in Timbuktu in 2012"
    )
    assert "completed the implementation of the reparations programme" in case.status
    assert case.outcome == (
        "ICC judges instructed him to pay EUR 2.7 million for reparations to the victims."
    )
    assert case.amount == "EUR 2.7 million"
    assert "convicted for the war crime" in case.evidence["litis"]


ICJ_HTML = """
<div class="view view-press-releases"><div class="view-content row">
<div class="views-row">
  <div class="views-field-field-press-release-number"><a
    href="/sites/default/files/case-related/200/release-en.pdf">
    Press release No. 2026/20</a></div>
  <div class="views-field-field-date-of-the-document"><time
    datetime="2026-07-21T12:00:00Z">21 July 2026</time></div>
  <div class="views-field-field-document-long-title"><p>
    Poland files a declaration of intervention under Article 63</p></div>
</div>
<div class="views-row">
  <div class="views-field-field-press-release-number"><a href="/old.pdf">
    Press release No. 2018/1</a></div>
  <div class="views-field-field-date-of-the-document"><time
    datetime="2018-01-10T12:00:00Z">10 January 2018</time></div>
  <div class="views-field-field-document-long-title">Historical release</div>
</div>
</div></div>
"""


def test_cij_html_parser_extracts_candidate_and_filters_by_since():
    items = CijCollector.parse(ICJ_HTML, date(2026, 6, 1))

    assert len(items) == 1
    item = items[0]
    assert item.source == "CIJ"
    assert item.authority == "Corte Internacional de Justicia"
    assert item.official_title == (
        "Poland files a declaration of intervention under Article 63"
    )
    assert item.source_id == "2026/20"
    assert item.url.endswith("/sites/default/files/case-related/200/release-en.pdf")
    assert item.case_number == "200"
    assert item.official_evidence == {"case_number": "200"}
    assert item.published_at == date(2026, 7, 21)
    assert "<" not in item.description
    assert not item.case_parties
    assert not item.case_claim


ICJ_CASE_HTML = """
<div class="view view-press-releases"><div class="view-content row">
<div class="views-row">
  <div class="views-field-field-press-release-number"><a
    href="/sites/default/files/case-related/193/193-20260801-pre-01-00-en.pdf">
    Press release No. 2026/25</a></div>
  <div class="views-field-field-date-of-the-document"><time
    datetime="2026-08-01T12:00:00Z">1 August 2026</time></div>
  <div class="views-field-field-document-long-title"><p>
    Application of the Genocide Convention in Sudan (Sudan v. United Arab Emirates)
    - The Court rejects the Request for provisional measures and removes the case
    from its List</p></div>
</div>
</div></div>
"""


def test_cij_parser_splits_caption_litis_outcome_and_terminal_status_literally():
    item = CijCollector.parse(ICJ_CASE_HTML, date(2026, 7, 1))[0]

    assert item.case_parties == "Sudan v. United Arab Emirates"
    assert item.case_number == "193"
    assert item.case_claim == "Application of the Genocide Convention in Sudan"
    assert item.case_outcome == (
        "The Court rejects the Request for provisional measures and removes the case "
        "from its List"
    )
    assert item.case_status == "removes the case from its List"
    assert not item.case_amount
    assert item.official_evidence["case_number"] == "193"
    assert item.official_evidence["case_caption"] == "(Sudan v. United Arab Emirates)"
    assert item.official_evidence["litis"] == item.case_claim
    assert item.official_evidence["outcome"] == item.case_outcome
    assert item.official_evidence["status"] == item.case_status


ICJ_PENDING_CASE_HTML = """
<div class="view view-press-releases"><div class="view-content row">
<div class="views-row">
  <div class="views-field-field-press-release-number"><a
    href="/sites/default/files/case-related/193/193-20260801-pre-01-00-en.pdf">
    Press release No. 2026/26</a></div>
  <div class="views-field-field-date-of-the-document"><time
    datetime="2026-08-02T12:00:00Z">2 August 2026</time></div>
  <div class="views-field-field-document-long-title"><p>
    Alleged Breaches of Certain International Obligations in respect of the Occupied
    Palestinian Territory (Nicaragua v. Germany) - Preliminary objections raised by
    Germany - Public hearings to be held from Monday 7 to Thursday 10 September 2026
  </p></div>
</div>
</div></div>
"""


def test_cij_parser_keeps_procedural_update_as_status_without_inventing_outcome():
    item = CijCollector.parse(ICJ_PENDING_CASE_HTML, date(2026, 7, 1))[0]

    assert item.case_parties == "Nicaragua v. Germany"
    assert item.case_number == "193"
    assert item.case_claim.startswith("Alleged Breaches")
    assert item.case_status == (
        "Preliminary objections raised by Germany - Public hearings to be held from "
        "Monday 7 to Thursday 10 September 2026"
    )
    assert not item.case_outcome


def test_cij_extractor_reads_explicit_case_block_from_stored_official_node():
    case = extract_international_case(
        "CIJ",
        "Poland files a declaration of intervention in the proceedings under Article 63",
        (
            "Document Number 200-20260630-PRE-01-00-EN Document Type press_release "
            "Case 200 - Alleged Smuggling of Migrants (Lithuania v. Belarus) "
            "Number (Press Release, Order, etc) 2026/18 Date of the Document "
            "Tue, 06/30/2026 - 12:00"
        ),
        "https://www.icj-cij.org/node/206437",
    )

    assert case.number == "200"
    assert case.parties == "Lithuania v. Belarus"
    assert case.claim == "Alleged Smuggling of Migrants"
    assert case.status.startswith("Poland files a declaration")
    assert not case.outcome
    assert case.evidence["case_number"] == "200"
    assert case.evidence["procedural_update"] == case.status


def test_cij_extractor_does_not_trust_stored_case_block_from_non_official_url():
    case = extract_international_case(
        "CIJ",
        "Declaration of intervention of Poland",
        "Case 200 - Alleged Smuggling of Migrants (Lithuania v. Belarus) Date of the Document",
        "https://example.test/node/206436",
    )

    assert not case.number
    assert not case.parties
    assert not case.claim
    assert not case.evidence


@pytest.mark.parametrize(
    "href",
    (
        "https://example.test/sites/default/files/case-related/193/file.pdf",
        "/sites/default/files/not-case-related/193/file.pdf",
        "/sites/default/files/case-related/press-release-193/file.pdf",
        "/sites/default/files/case-related/193/file.html",
    ),
)
def test_cij_parser_does_not_infer_case_number_from_non_official_or_non_case_url(href):
    payload = ICJ_CASE_HTML.replace(
        "/sites/default/files/case-related/193/193-20260801-pre-01-00-en.pdf",
        href,
    )

    items = CijCollector.parse(payload, date(2026, 7, 1))

    if href.startswith("https://example.test"):
        assert items == []
        return
    item = items[0]

    assert not item.case_number
    assert "case_number" not in item.official_evidence


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


def test_diputados_parser_index_items_use_permanent_dated_url():
    # Regresión #11: "gp_hoy.html#Iniciativa1" deja de resolver el asunto en
    # cuanto la portada avanza al día siguiente. La URL emitida debe ser la
    # edición archivada y fechada de la Gaceta (confirmada en vivo: responde
    # 200 y conserva el mismo ancla que la portada del día).
    items = DiputadosCollector.parse(GACETA_CON_ANEXOS, date(2026, 6, 1))

    index_items = [item for item in items if item.document_type != "Dictámenes y minutas"]
    assert len(index_items) == 1
    assert index_items[0].url == (
        "https://gaceta.diputados.gob.mx/Gaceta/66/2026/jul/20260706.html#Iniciativa1"
    )


def test_permanent_gaceta_url_omits_anchor_when_missing():
    assert (
        permanent_gaceta_url(date(2026, 1, 15))
        == "https://gaceta.diputados.gob.mx/Gaceta/66/2026/ene/20260115.html"
    )


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


def test_always_relevant_portals_reflect_owner_scope():
    # v5 (ponderación con navegador residencial): PROFECO entra —su archivo
    # de prensa y alertas en gob.mx publica actos con relevancia jurídica y
    # el dueño pidió expresamente los descentralizados de consumo—, junto
    # con CNBV, UIF y la nueva CNA (antimonopolio). El espejo gob.mx de
    # CONDUSEF sigue fuera: no publica contenido propio (su sitio real,
    # condusef.gob.mx, quedó diferido por cadena TLS rota).
    assert {"prodecon", "cnbv", "uif", "antimonopolio", "profeco"} <= ALWAYS_RELEVANT_PORTALS
    assert "condusef" not in ALWAYS_RELEVANT_PORTALS


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
    assert item.url.startswith("https://www.bancomundial.org/")
    assert item.canonical_url == item.url
    assert "Uribe" in item.description


def test_worldbank_url_only_promotes_exact_official_hosts_to_https():
    assert canonical_worldbank_url("http://documents1.worldbank.org/report.pdf") == (
        "https://documents1.worldbank.org/report.pdf"
    )
    assert canonical_worldbank_url("http://www.worldbank.org/en/news") == (
        "https://www.worldbank.org/en/news"
    )
    assert canonical_worldbank_url("http://worldbank.example/en/news") == (
        "http://worldbank.example/en/news"
    )


def test_worldbank_parser_filters_by_since():
    items = WorldBankCollector.parse(WB_PAYLOAD, date(2026, 1, 1))
    urls = [item.url for item in items]
    assert not any("2019" in url for url in urls)


def test_worldbank_parser_rejects_external_url():
    payload = {
        "documents": {
            "doc": {
                "id": "doc",
                "url": "https://evil.example/noticia",
                "title": {"cdata!": "Comunicado aparente"},
                "descr": {"cdata!": "Descripción aparente"},
                "lnchdt": "2026-07-01T18:04:00Z",
            }
        }
    }

    assert WorldBankCollector.parse(payload, date(2026, 6, 1)) == []


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


def test_icsid_first_run_bootstraps_without_emitting_historical_inventory():
    proposal = IcsidCollector.propose(
        ICSID_PAYLOAD,
        previous=None,
        today=date(2026, 7, 7),
    )

    assert proposal.bootstrap is True
    assert proposal.candidates == ()
    assert {item.case_number for item in proposal.historical_candidates} == {
        "ARB/25/7",
        "ARB/25/32",
    }
    assert set(proposal.state) == {"ARB/25/7", "ARB/25/32"}
    assert "Pending" in proposal.state["ARB/25/7"]
    assert "Mozambique" not in json.dumps(proposal.state)


def test_icsid_dry_run_does_not_consume_novelty(tmp_path):
    snapshot_path = tmp_path / "icsid_snapshot.json"

    # Parsear o ejecutar dry-run nunca escribe estado ni convierte el
    # inventario inicial en novedades del día.
    items = IcsidCollector.parse(
        ICSID_PAYLOAD,
        snapshot_path=snapshot_path,
        today=date(2026, 7, 7),
        persist_snapshot=False,
    )

    assert items == []
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

    emitted_casenos = {item.case_number for item in items}
    assert emitted_casenos == {"ARB/25/32", "ARB/26/99"}
    for item in items:
        assert item.published_at == date(2026, 7, 8)

    # El parser propone cambios, pero no consume el snapshot anterior.
    saved = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert saved == {"ARB/25/7": "Pending", "ARB/25/32": "Concluded"}


ICSID_CASE_DETAIL_HTML = """
<div class="case__detail">
<h1>Canadian Pacific Kansas City Limited v. United Mexican States
 (ICSID Case No. ARB/26/19)</h1>
<ul>
<li class="row"><div><label>Subject of Dispute:</label></div>
<div class="rightcol">Railroad concession</div></li>
<li class="row"><div><label>Economic Sector:</label></div>
<div class="rightcol">Transportation</div></li>
<li class="row"><div><label>Instrument(s) Invoked:</label></div>
<div class="rightcol">Comprehensive and Progressive Agreement for
 Trans-Pacific Partnership (CPTPP)</div></li>
<li class="row"><div><label>Date Registered:</label></div>
<div class="rightcol">April 27, 2026</div></li>
<li class="row"><div><label>Status of Proceeding:</label></div>
<div class="rightcol">Pending</div></li>
<li class="row"><div><label>Latest Development:</label></div>
<div class="rightcol">July 3, 2026 - Arbitrator appointed.</div></li>
</ul></div>
"""


def test_icsid_case_detail_preserves_cptpp_evidence_without_tmec_inference():
    detail = parse_case_detail(ICSID_CASE_DETAIL_HTML)

    assert detail.subject == "Railroad concession"
    assert detail.treaty.endswith("(CPTPP)")
    assert "USMCA" not in detail.treaty
    assert "T-MEC" not in detail.treaty
    assert detail.registered_at == date(2026, 4, 27)
    assert detail.latest_development_at == date(2026, 7, 3)


def test_icsid_snapshot_path_configurable_via_env(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "env_snapshot.json"
    monkeypatch.setenv("RADAR_ICSID_SNAPSHOT", str(snapshot_path))

    collector = IcsidCollector(client=None)

    assert collector.snapshot_path == snapshot_path


# --- TFJA: acuerdos de Sala Superior (fragmento fiel, corrida en vivo 07-jul-2026)

TFJA_HTML = """
<p><strong>Miercoles 17 de junio de 2026</strong></p>
<ul>
<li><a href="/pdf/secretaria_general_de_acuerdos/acuerdos_sala_superior/2026/SS-12-2026.pdf">
SS/12/2026</a><br />
HORARIO DE GUARDIAS DE LAS OFICIAL&Iacute;AS DE PARTES DEL TRIBUNAL</li>
<li><a href="/pdf/secretaria_general_de_acuerdos/acuerdos_sala_superior/2026/SS-11-2026.pdf">
SS/11/2026</a><br />
SUSPENSI&Oacute;N DE ACTIVIDADES PRESENCIALES</li>
</ul>
<p><strong>Lunes 15 de junio de 2026</strong></p>
<ul>
<li><a href="/pdf/x/SRT-04-2026.pdf">SRT/04/2026</a><br /> ACUERDO ANTIGUO</li>
</ul>
"""


def test_tfja_parser_assigns_dates_and_filters_by_since():
    from app.sources.tfja import TfjaCollector

    items = TfjaCollector.parse(TFJA_HTML, date(2026, 6, 16))

    assert [item.published_at for item in items] == [date(2026, 6, 17), date(2026, 6, 17)]
    assert items[0].url.endswith("/2026/SS-12-2026.pdf")
    assert items[0].official_title.startswith("Acuerdo SS/12/2026 del TFJA.")
    assert "GUARDIAS" in items[0].description
    assert items[0].source == "TFJA"


# --- Secretariado T-MEC: tabla de paneles (estructura fiel, 07-jul-2026)

TMEC_HTML = """
<table><tr><th>Panel Review Number</th><th>Title</th><th>Instrument Invoked</th>
<th>Dispute Settlement Mechanism</th><th>Complaining Party</th><th>Responding Party</th>
<th>Third Party</th><th>Panel Composition</th><th>Date of Panel Request</th>
<th>Status</th><th>Date of Final Report</th></tr>
<tr><td>MEX-USA-2024-31A-04</td><td>Rapid Response Labor Panel</td>
<td>Canada United States Mexico Agreement (CUSMA)</td><td>Annex 31-A</td>
<td>United States</td><td>Mexico</td><td>N/A</td>
<td><ul><li>Adolfo Ciudad, Chair</li><li>Janice Bellace, panelist</li></ul></td>
<td>2024-12-18</td><td>Active</td><td>N/A</td></tr></table>
"""


def test_tmec_bootstrap_is_silent_and_diff_maps_columns_by_headers():
    from app.sources.tmec import TmecCollector

    pages = [(TMEC_HTML, TmecCollector.urls[1])]
    bootstrap = TmecCollector.propose(pages, previous=None, today=date(2026, 7, 7))
    assert bootstrap.bootstrap is True
    assert bootstrap.candidates == ()
    assert len(bootstrap.historical_candidates) == 1
    historical = bootstrap.historical_candidates[0]
    assert historical.source_id == "MEX-USA-2024-31A-04"
    assert historical.case_status == "Active"
    assert "Adolfo Ciudad" not in historical.case_status
    assert historical.official_evidence["panel_composition"].startswith("Adolfo Ciudad")

    # Segunda corrida sin cambios: nada nuevo.
    same = TmecCollector.propose(
        pages,
        previous=bootstrap.state,
        today=date(2026, 7, 8),
    )
    assert same.candidates == ()

    # Cambio de estatus: se emite.
    changed = TMEC_HTML.replace(">Active<", ">Completed<")
    again = TmecCollector.propose(
        [(changed, TmecCollector.urls[1])],
        previous=bootstrap.state,
        today=date(2026, 7, 9),
    )
    assert len(again.candidates) == 1
    item = again.candidates[0]
    assert item.case_status == "Completed"
    assert item.case_parties == "United States / Mexico"
    assert item.case_number == "MEX-USA-2024-31A-04"
    assert item.official_evidence["panel_composition"].startswith("Adolfo Ciudad")
    assert item.case_status not in item.official_evidence["panel_composition"]
    assert "case=MEX-USA-2024-31A-04" in item.canonical_url
    assert item.official_evidence["case_listing"] == item.canonical_url


def test_tmec_dry_run_does_not_persist_snapshot(tmp_path):
    from app.sources.tmec import TmecCollector

    snapshot = tmp_path / "tmec.json"
    TmecCollector.parse(
        TMEC_HTML, snapshot_path=snapshot, today=date(2026, 7, 7), persist_snapshot=False
    )
    assert not snapshot.exists()


def test_tmec_rejects_unknown_status_instead_of_publishing_panelists_as_status():
    from app.sources.tmec import parse_tmec_page

    malformed = TMEC_HTML.replace(">Active<", ">Adolfo Ciudad, Chair<")
    with pytest.raises(SourceContractError, match="estatus desconocido"):
        parse_tmec_page(malformed, "https://example.test/chapter-31")


def test_anam_skips_listing_pages():
    from app.sources.anam import AnamCollector

    feed = b"""<rss version="2.0"><channel>
    <item><guid>https://www.anam.gob.mx/comunicados-de-prensa-2026/</guid>
    <link>https://www.anam.gob.mx/comunicados-de-prensa-2026/</link>
    <title>Comunicados de Prensa 2026</title>
    <pubDate>Sat, 04 Jul 2026 19:05:45 +0000</pubDate></item>
    <item><guid>https://www.anam.gob.mx/comunicado-prensa-conjunto-38-2026/</guid>
    <link>https://www.anam.gob.mx/comunicado-prensa-conjunto-38-2026/</link>
    <title>ASEGURAMIENTO DE CIGARRILLOS EN ADUANA DEL AIFA</title>
    <pubDate>Sat, 04 Jul 2026 18:58:10 +0000</pubDate></item>
    </channel></rss>"""

    items = AnamCollector.parse(feed, date(2026, 7, 1))

    assert len(items) == 1
    assert items[0].source == "ANAM"
    assert "ASEGURAMIENTO" in items[0].official_title


def test_rss_zero_is_valid_only_when_channel_structure_is_present():
    assert OnuNoticiasCollector.parse(
        b"<rss version='2.0'><channel></channel></rss>",
        date(2026, 8, 1),
    ) == []

    with pytest.raises(SourceContractError, match="canal RSS"):
        OnuNoticiasCollector.parse(b"<html><body>Access denied</body></html>", date(2026, 8, 1))


def test_rss_and_dof_report_malformed_blocks_for_degraded_coverage():
    malformed = b"<rss><channel><item><title>broken</item></channel></rss>"
    assert OnuNoticiasCollector.parse_with_diagnostics(
        malformed, date(2026, 8, 1)
    ) == ([], 1)

    dof = (
        b"<rss><channel><item><title>SHCP</title><description>Acuerdo</description>"
        b"<link>https://dof.gob.mx/nota_detalle.php?codigo=1&amp;fecha=12/08/2026</link>"
        b"<valueDate>12/08/2026</valueDate><broken></item></channel></rss>"
    )
    assert DofCollector.parse_with_diagnostics(dof, date(2026, 8, 1)) == ([], 1)


def test_rss_reports_well_formed_but_incomplete_items_as_degraded():
    incomplete = b"""<rss><channel>
    <item><title>Missing link</title>
      <pubDate>Wed, 12 Aug 2026 12:00:00 GMT</pubDate></item>
    <item><title>Invalid date</title><link>https://news.un.org/example</link>
      <pubDate>not-a-date</pubDate></item>
    </channel></rss>"""

    assert OnuNoticiasCollector.parse_with_diagnostics(
        incomplete, date(2026, 8, 1)
    ) == ([], 2)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/rss+xml"},
            content=incomplete,
        )

    async def run() -> OnuNoticiasCollector:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            collector = OnuNoticiasCollector(client)
            assert await collector.collect(date(2026, 8, 1)) == []
            return collector

    collector = asyncio.run(run())
    assert collector.diagnostics.status == "degraded"
    assert "2 bloques RSS" in collector.diagnostics.warnings[0]


def test_html_collectors_reject_unexpected_pages_instead_of_false_green_zero():
    with pytest.raises(SourceContractError, match="estructura HTML"):
        TradeGovCollector.parse("<html><h1>Maintenance</h1></html>", date(2026, 8, 1))
    with pytest.raises(SourceContractError, match="estructura HTML"):
        CijCollector.parse("<html><h1>Maintenance</h1></html>", date(2026, 8, 1))


class _ContractCollector(Collector):
    source = "Fixture"

    async def collect(self, since: date):
        del since
        return []


def test_response_contract_rejects_wrong_content_type_even_on_http_200():
    collector = _ContractCollector(client=None)
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://example.test/source"),
        headers={"content-type": "text/html; charset=utf-8"},
        content=b"<html>login</html>",
    )

    with pytest.raises(SourceContractError, match="Content-Type inesperado"):
        collector.validate_response(response, content_types={"application/json"})


def test_gobmx_partial_403_is_exposed_as_degraded():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request, text="Forbidden")

    async def run() -> GobMxCollector:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            collector = GobMxCollector(client)
            items = await collector._collect_archive("se", "prensa", date(2026, 8, 1))
            assert items == []
            return collector

    collector = asyncio.run(run())
    assert collector.diagnostics.status == "degraded"
    assert "403" in collector.diagnostics.warnings[0]


def test_gobmx_unexpected_http_200_body_is_degraded_not_false_green():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/javascript"},
            text="window.unexpected = true;",
        )

    async def run() -> GobMxCollector:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            collector = GobMxCollector(client)
            assert await collector._collect_archive(
                "se", "prensa", date(2026, 8, 1)
            ) == []
            return collector

    collector = asyncio.run(run())
    assert collector.diagnostics.status == "degraded"
    assert "estructura de archivo" in collector.diagnostics.warnings[0]
    assert collector.diagnostics.validated_endpoints == 1


def test_gobmx_structural_empty_archive_is_a_valid_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/javascript"},
            text='$("#prensa").append("");',
        )

    async def run() -> GobMxCollector:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            collector = GobMxCollector(client)
            assert await collector._collect_archive(
                "se", "prensa", date(2026, 8, 1)
            ) == []
            return collector

    collector = asyncio.run(run())
    assert collector.diagnostics.status == "certified"
    assert collector.diagnostics.validated_endpoints == 1


def test_rss_http_404_is_an_error_not_a_valid_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            request=request,
            headers={"content-type": "text/html"},
            text="Not found",
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            collector = OnuNoticiasCollector(client)
            with pytest.raises(httpx.HTTPStatusError):
                await collector.collect(date(2026, 8, 1))

    asyncio.run(run())


def test_certification_gate_requires_exactly_the_18_sources_without_degradation():
    healthy = [
        {
            "source": source,
            "status": "ok",
            "items_found": 0,
            "error": None,
            "validated_endpoints": 1,
        }
        for source in RELAUNCH_SOURCES
    ]
    report = certification_report(healthy)
    assert report["required_sources"] == 18
    assert report["relaunch_ready"] is True

    degraded = [dict(item) for item in healthy]
    degraded[5]["status"] = "degraded"
    report = certification_report(degraded)
    assert report["relaunch_ready"] is False
    assert report["counts"]["degraded"] == 1

    unvalidated = [dict(item) for item in healthy]
    unvalidated[0].pop("validated_endpoints")
    report = certification_report(unvalidated)
    assert report["relaunch_ready"] is False
    assert report["sources"][0]["status"] == "degraded"


def test_pipeline_never_reports_ok_without_a_validated_endpoint():
    class UnvalidatedCollector(Collector):
        source = "Fixture sin contrato"

        async def collect(self, since: date):
            del since
            return []

    result = asyncio.run(
        _collect_source(
            UnvalidatedCollector(client=None),
            date(2026, 8, 1),
            attempts=1,
            backoff_seconds=0,
        )
    )

    status = result.status_dict()
    assert status["status"] == "degraded"
    assert status["validated_endpoints"] == 0
    assert "ningún endpoint" in status["warnings"][0]


def test_pipeline_deadline_degrades_only_the_hanging_source() -> None:
    class HangingCollector(Collector):
        source = "Fuente lenta"

        def __init__(self) -> None:
            super().__init__(client=None)
            self.unfinished_work = {"queued_pages": 7}

        async def collect(self, since: date):
            del since
            await asyncio.Event().wait()

    class FastCollector(Collector):
        source = "Fuente rápida"

        async def collect(self, since: date):
            del since
            self.diagnostics.validated_endpoints = 1
            return []

    results = asyncio.run(
        _collect_results(
            [HangingCollector(), FastCollector(client=None)],
            date(2026, 8, 1),
            attempts=1,
            backoff_seconds=0,
            source_deadline_seconds=0.01,
            collection_deadline_seconds=0.5,
        )
    )

    assert [result.status_dict()["status"] for result in results] == ["degraded", "ok"]
    assert results[0].details["unfinished_work"] == {"queued_pages": 7}
    assert results[0].details["deadline_scope"] == "fuente"


def test_pipeline_total_deadline_marks_only_unfinished_collectors() -> None:
    class HangingCollector(Collector):
        source = "Fuente lenta"

        async def collect(self, since: date):
            del since
            await asyncio.Event().wait()

    class FastCollector(Collector):
        source = "Fuente rápida"

        async def collect(self, since: date):
            del since
            self.diagnostics.validated_endpoints = 1
            return []

    results = asyncio.run(
        _collect_results(
            [FastCollector(client=None), HangingCollector(client=None)],
            date(2026, 8, 1),
            attempts=1,
            backoff_seconds=0,
            source_deadline_seconds=1,
            collection_deadline_seconds=0.01,
        )
    )

    assert [result.status_dict()["status"] for result in results] == ["ok", "degraded"]
    assert results[1].details["deadline_scope"] == "colección completa"


def test_dof_previous_day_review_requires_a_healthy_validated_result():
    today = date(2026, 8, 12)
    since = date(2026, 8, 11)
    healthy = SourceResult(source="DOF", details={"validated_endpoints": 1})

    assert _dof_previous_day_reviewed([healthy], since, today) is True

    for result in (
        SourceResult(source="DOF", degraded=True, details={"validated_endpoints": 1}),
        SourceResult(source="DOF", error="falló", details={"validated_endpoints": 1}),
        SourceResult(source="DOF", details={"validated_endpoints": 0}),
    ):
        assert _dof_previous_day_reviewed([result], since, today) is False
