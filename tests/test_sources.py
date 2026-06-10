from datetime import date

from app.sources.dof import DofCollector
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
