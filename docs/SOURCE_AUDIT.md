# Auditoría de fuentes oficiales

Verificación realizada el 10 de junio de 2026.

| Fuente | Interfaz utilizada | Estado |
|---|---|---|
| Diario Oficial de la Federación | RSS `sumarios/sumario_31dias.xml` | Estructurado y público |
| PLATIICA | WordPress REST `wp-json/wp/v2/posts` | Estructurado; requiere corregir enlaces internos |
| Senado de la República | JSON anual de iniciativas y proposiciones | Estructurado y público |
| SNICE | Página pública de inicio y actualidad | HTML |
| Gaceta Parlamentaria de Diputados | `gp_hoy.html` | HTML con índice semántico |
| Instituto Mexicano de la Propiedad Industrial | Sección pública "Lo más nuevo" | HTML |
| Portales de la Administración Pública Federal en `gob.mx` | Sitemap gubernamental y archivos institucionales de prensa y artículos | XML + HTML |

## Decisiones

- El recolector del DOF consume únicamente el sumario RSS. No descarga el texto
  de `nota_detalle.php`, ruta restringida en `robots.txt`.
- Los enlaces de PLATIICA devueltos por REST apuntan ocasionalmente a una IP
  privada. Se reconstruyen con el dominio público y el `slug`.
- Los recolectores HTML tienen pruebas separadas y pueden fallar sin bloquear
  las demás fuentes.
- El Senado se consulta desde su JSON de transparencia para incluir iniciativas,
  no únicamente normas ya publicadas.
- Todo resultado conserva el enlace oficial y se filtra por vocabulario
  jurídico configurable antes de resumirse.
- `gob.mx` no expone una API o archivo global de publicaciones. El recolector
  descubre los portales mediante `sitemap-gobierno.xml`, excluye campañas no
  institucionales, recorre sus archivos recientes y descarga el texto completo
  únicamente cuando el título contiene señales jurídicas relevantes.
- El índice de sitemaps observado el 10 de junio de 2026 llega sin etiqueta XML
  de cierre. Su lector extrae las ubicaciones completas de forma tolerante.

## Alcance administrativo y contencioso

- **Derecho administrativo:** LFPA, LOAPF, reglamentos interiores, estatutos
  orgánicos, manuales, estructura, sectorización, creación o extinción de
  organismos y delegación de facultades.
- **Nombramientos federales:** una publicación debe contener tanto una acción
  de nombramiento, designación, ratificación, remoción, suplencia o encargaduría
  como un cargo de mando o titularidad federal.
- **Contencioso administrativo:** LFPCA, juicios de nulidad, sentencias,
  incidentes, medidas cautelares y actuaciones jurisdiccionales del TFJA.
- **Contencioso administrativo fiscal:** se asigna únicamente cuando existe
  contenido contencioso administrativo y además materia fiscal, o una figura
  contenciosa fiscal expresa.
- El nombre de una autoridad no determina por sí solo la materia. Por ejemplo,
  una compra de equipo de Bancomext no se clasifica como comercio exterior.

## Fuentes previstas para siguientes fases

- Comunicados y criterios sustantivos de PRODECON.
- Novedades normativas y técnicas del SAT.
- Gaceta de la Propiedad Industrial y documentos descargables del IMPI.
- Anteproyectos y consultas públicas regulatorias de autoridades federales.
- Jurisprudencia y comunicados relevantes del TFJA y la SCJN.

Cada incorporación debe revisar primero API, RSS, sitemap o datos abiertos y
documentar límites de uso antes de implementar extracción HTML.

## Fuentes internacionales (primera ola)

Verificación intentada el 7 de julio de 2026. La red hacia sitios externos
estaba bloqueada por proxy en el entorno de desarrollo (solo `WebSearch`
respondió; `WebFetch` devolvió 403 incluso contra dominios de control como
`example.com`), así que las tres fuentes se implementaron y probaron contra
fixtures locales, no contra los feeds en vivo.

| Fuente | Interfaz utilizada | Evidencia de la URL elegida | Estado |
|---|---|---|---|
| ONU Noticias | RSS 2.0, `news.un.org/feed/subscribe/es/news/all/rss.xml` | La página oficial de feeds `news.un.org/es/rss-feeds` lista el feed general en español; el patrón de ruta `/feed/subscribe/{idioma}/{sección}/all/...` se confirmó con el feed hermano de audio `news.un.org/feed/subscribe/es/audio-product/all/audio-rss.xml`, indexado y citado en los resultados de búsqueda. | Pendiente de verificación en vivo desde la Mac |
| USTR | RSS 2.0, `ustr.gov/rss.xml` (ruta convencional de Drupal) | No se localizó, vía búsqueda, un feed RSS activo para la sección actual de comunicados (`ustr.gov/about-us/policy-offices/press-office/press-releases`). El único recurso RSS de USTR indexado es el índice archivado `ustr.gov/archive/Meta_Content/RSS/Section_Index.html`, previo al rediseño del sitio. Se usó la ruta por defecto que Drupal expone para el feed general del sitio, sin poder confirmarla con una petición real. | Pendiente de verificación en vivo desde la Mac; sustituir la URL si al probarla con red real no responde con RSS válido |
| Trade.gov | RSS 2.0, `blog.trade.gov/feed/` | La búsqueda confirmó a Tradeology, el blog oficial de la International Trade Administration sobre política comercial (incluida la relación EEUU-México), con su feed en `blog.trade.gov/feed/`. No se localizó un feed RSS dedicado para la sección HTML `trade.gov/press-releases`. | Pendiente de verificación en vivo desde la Mac |

Decisiones:

- Los tres recolectores (`app/sources/international.py`) comparten una clase
  base `RssCollector` con el mismo parser tolerante que usa el DOF: bloques
  `<item>` aislados por expresión regular y `ElementTree.fromstring` con
  `try`/`except` por bloque, de modo que un ítem dañado no descarta el resto
  del feed. Cada fuente conserva su propia clase, su propio atributo `source`
  y falla de forma aislada en `app/pipeline.py` (`_collect_source` ya envuelve
  cada colector con reintentos independientes).
- Campos: `title` → `official_title`; `description` limpiada de HTML con
  `clean_text` (BeautifulSoup) cuando trae etiquetas o CDATA; `link` → `url`;
  `pubDate` (RFC 822) → `published_at`, parseado con
  `email.utils.parsedate_to_datetime`; `guid` (o `link` si falta) → base del
  `source_id`, con el mismo esquema de hash SHA-256 truncado a 16 caracteres
  que usan IMPI, SNICE, Diputados y Senado.
- Se descartan ítems sin título o cuyo enlace no comience con `http://` o
  `https://`, y los anteriores a `since`.
- `app/taxonomy.py` ya incluía, desde la fase de núcleo del radar, las
  entradas de `ORGAN_CATALOG` y `SOURCE_ORIGIN` para "ONU Noticias", "USTR" y
  "Trade.gov" (jurisdicción `internacional`, país u organismo `ONU`/`EEUU`).
  Este trabajo añadió el vocabulario en inglés a `app/relevance.py` para que
  el clasificador no descarte por idioma el contenido de estas fuentes.
- Vocabulario en inglés agregado a `CATEGORY_TERMS` (sin quitar términos en
  español): `tariff`, `tariffs`, `trade agreement`, `usmca`, `free trade`,
  `trade remedy`, `antidumping duty`, `section 301`, `export control`,
  `sanction`, `sanctions`, `wto` en "Comercio exterior"; `customs`,
  `customs duties` en "Aduanero"; `tax`, `taxation`, `transfer pricing` en
  "Fiscal"; `patent`, `trademark`, `copyright`, `intellectual property` en
  "Propiedad intelectual". Además `usmca`, `tariff`, `trade agreement` en
  `HIGH_VALUE_TERMS`.

### Catálogo fase 2 (pendiente de implementación)

Siguiendo la regla de `AGENTS.md` ("Prefer official APIs or feeds; use HTML
parsing only when no structured interface exists"), cada fuente de este
catálogo debe documentarse (API/RSS/sitemap, formato, límites de tasa y
autenticación) **antes** de escribir el recolector:

- **OCDE (OECD).** Publicaciones y comunicados sobre México y comercio
  internacional; existen feeds RSS por tema en `oecd.org` y un catálogo de
  datos abierto (OECD.Stat / SDMX). Documentar el feed específico de política
  fiscal/comercial y el límite de frecuencia antes de integrarlo.
- **OMC (WTO).** Ya se detectó en esta fase un "RSS feeds gateway" en
  `wto.org/english/res_e/webcas_e/rss_e.htm` (y su equivalente en español);
  cubre disputas, notificaciones y comunicados de prensa relevantes para
  México. Confirmar cuál subfeed cubre disputas y medidas comerciales antes de
  implementar.
- **FMI (IMF).** Comunicados de prensa y informes de artículo IV sobre
  México; el sitio `imf.org` publica RSS por tipo de contenido. Documentar el
  feed de comunicados de prensa y el de "Country Focus" para México.
- **Banco Mundial (World Bank).** Comunicados de prensa y publicaciones sobre
  México vía su API de datos abiertos (`api.worldbank.org`) y RSS de noticias;
  documentar el endpoint y su formato (JSON/XML) antes de construir el
  extractor.
- **CIADI / ICSID.** Casos de arbitraje de inversión que involucren a México
  (o a partes mexicanas). ICSID publica un listado de casos con detalle por
  caso en `icsid.worldbank.org`; no se ha confirmado un RSS o API pública
  estructurada, por lo que probablemente requiera HTML parsing documentado
  como excepción explícita. El contrato del extractor de casos debe capturar,
  como mínimo:
  - **Partes**: demandante(s) y demandado (Estado), y su representación.
  - **Litis**: materia en disputa (tratado invocado, medida impugnada).
  - **Estado/resultado procesal**: pendiente, en audiencia, laudo emitido,
    anulación solicitada, etc.
  - **Razonamiento esencial**: fundamento central del laudo o decisión
    procesal relevante (resumen, no el documento íntegro).
  - **Monto en controversia**: cantidad reclamada y, si existe, monto del
    laudo.
- **Arreglo de Wassenaar (Wassenaar Arrangement).** Listas de control de
  exportación de bienes de doble uso y armas convencionales; el sitio
  `wassenaar.org` publica documentos públicos (listas de control, mejores
  prácticas) sin API o RSS conocido. Documentar el mecanismo de publicación
  (probablemente descarga periódica de PDF/HTML) y su cadencia antes de
  integrarlo.
- **Corte Penal Internacional (CPI/ICC).** Comunicados de prensa y
  actuaciones relevantes; `icc-cpi.int` publica comunicados de prensa con
  posible RSS. Documentar el feed y acotar el alcance a asuntos con
  relevancia para México (p. ej. situaciones remitidas por México, o de
  interés para su política exterior) antes de implementar, dado el volumen
  general del sitio.
- **Corte Internacional de Justicia (CIJ/ICJ).** Comunicados de prensa y
  resúmenes de fallos en `icj-cij.org`; documentar si existe RSS o solo HTML,
  y acotar a casos donde México sea parte o tengan relevancia regulatoria
  directa.

Cada una de estas fuentes debe pasar por el mismo ciclo que las anteriores:
confirmar la interfaz oficial con evidencia verificable, documentar límites de
uso en esta sección, y solo entonces escribir el recolector con sus fixtures y
pruebas offline.
