# Auditoría de fuentes oficiales

Auditoría v4 realizada el 7 de julio de 2026, con red externa disponible desde
el propio entorno de desarrollo. A diferencia de auditorías previas, cada
fuente de esta revisión (colectores nuevos y arreglo de TLS) se verificó
**en vivo**: peticiones HTTP/HTTPS reales contra los endpoints de producción,
no solo contra fixtures. Las fuentes heredadas (DOF, PLATIICA, Senado, SNICE,
Diputados, IMPI, gob.mx, ONU/USTR/Trade.gov) también se volvieron a probar en
vivo como parte de la corrida integral del punto 10; su estado se anota abajo.

## Tabla maestra

| Fuente | Interfaz verificada | Estado (7 jul 2026) |
|---|---|---|
| Diario Oficial de la Federación | RSS `sumarios/sumario_31dias.xml` | OK en vivo tras el fix de TLS (ver abajo); 4090 registros revisados en la ventana de prueba, 28 publicados |
| Senado de la República | JSON anual de iniciativas y proposiciones | OK en vivo tras el fix de TLS; 0 iniciativas en la ventana de prueba (no hubo iniciativas nuevas, no es error) |
| PLATIICA | WordPress REST `wp-json/wp/v2/posts` | OK en vivo; 0 registros en la ventana de prueba |
| SNICE | Página pública de inicio y actualidad | OK en vivo; 3 registros |
| Gaceta Parlamentaria de Diputados | `gp_hoy.html` (índice + anexos, ver punto 5) | OK en vivo; 21 registros (19 índice + 2 anexos) |
| Instituto Mexicano de la Propiedad Industrial | Sección pública "Lo más nuevo" | OK en vivo (200); 0 candidatos en la ventana de prueba |
| Portales de la Administración Pública Federal en `gob.mx` | Sitemap gubernamental + archivos de prensa/artículos | OK en vivo; 13 registros (incluye PRODECON siempre-relevante, ver punto 6) |
| ONU Noticias | RSS `news.un.org/feed/subscribe/es/news/all/rss.xml` | OK en vivo (200; confirmado en esta auditoría, antes solo probado con fixtures); 17 registros |
| USTR | RSS `ustr.gov/rss.xml` | OK en vivo (200; confirmado en esta auditoría); 5 registros |
| Trade.gov | RSS `blog.trade.gov/feed/` | Responde 200 pero el feed de Tradeology quedó obsoleto: redirige a una página HTML (`www.trade.gov/tradeology-official-ita-blog`), no a XML. El parser tolerante no encuentra bloques `<item>` y devuelve 0 candidatos sin error. Existe un feed sitewide funcional en `www.trade.gov/rss.xml`, pero mezcla contenido no filtrado por tema (se detectó, por ejemplo, un nodo de prueba interno); decidir su adopción queda fuera del alcance de esta tarea — se documenta para una futura revisión de `app/sources/international.py` |
| **CIADI (ICSID)** — nuevo | JSON `icsid.worldbank.org/api/all/cases` | OK en vivo; 1149 casos totales, 56 de México (21 Pending, 35 Concluded); primera corrida emitió 21 (todos los Pending) |
| **Banco Mundial** — nuevo | JSON `search.worldbank.org/api/v2/news` | OK en vivo; 1 registro en la ventana de prueba |
| **CPI (ICC)** — nuevo | RSS `icc-cpi.int/rss.xml` | OK en vivo; 8 registros |
| **CIJ (ICJ)** — nuevo | RSS `icj-cij.org/rss.xml` | OK en vivo; 3 registros |

Los conteos de "registros revisados" son candidatos que el colector emitió
antes del filtro de relevancia (`app/relevance.py`, fuera de alcance de esta
tarea salvo el punto 6); no todos llegan a publicarse — igual que ya ocurría
con el DOF (4090 revisados, 28 publicados). No es una regresión de esta
auditoría, es el comportamiento ya existente del pipeline.

## 1. Certificados TLS intermedios (DOF y Senado)

**Problema confirmado en vivo:** `www.dof.gob.mx` y
`transparenciaparlamentaria.senado.gob.mx` fallan la verificación TLS en este
entorno Linux con `unable to get local issuer certificate` /
`unable to verify the first certificate`. Con
`openssl s_client -showcerts` contra ambos hosts se confirmó que el servidor
manda **solo el certificado hoja** (profundidad 0), sin el intermedio. El
almacén de confianza del sistema (`/etc/ssl/certs/ca-certificates.crt`, que es
lo que usa `truststore` en Linux) sí contiene las raíces correspondientes, así
que basta con aportar el intermedio faltante.

Cadenas identificadas y resueltas:

- **`www.dof.gob.mx`**: hoja emitida por *Go Daddy Secure Certificate
  Authority - G2*. Se obtuvo el intermedio de la URL "CA Issuers" (AIA) del
  propio certificado hoja
  (`http://certificates.godaddy.com/repository/gdig2.crt`, servido también
  por HTTPS en `https://certs.godaddy.com/repository/gdig2.crt`), se
  convirtió de DER a PEM con `openssl x509 -inform DER`. Su emisor, *Go Daddy
  Root Certificate Authority - G2*, ya está en el almacén del sistema.
- **`transparenciaparlamentaria.senado.gob.mx`**: hoja emitida por *DigiCert
  Global G2 TLS RSA SHA256 2020 CA1*. Intermedio obtenido de
  `https://cacerts.digicert.com/DigiCertGlobalG2TLSRSASHA2562020CA1-1.crt`.
  Su emisor, *DigiCert Global Root G2*, ya está en el almacén del sistema.

Ambos intermedios se empaquetaron en `app/certs/intermediates.pem` (vigentes
hasta 2031) y se cargan en `app/pipeline.py` con
`ssl_context.load_verify_locations(cafile=...)` sobre el `SSLContext` de
`truststore`, con guard de existencia del archivo. `pyproject.toml` declara
`[tool.setuptools.package-data] app = ["certs/*.pem"]` para que el paquete
distribuido incluya el archivo — verificado construyendo el wheel
(`pip wheel . --no-deps`) e inspeccionando su contenido.

**Verificación en vivo (antes/después):** sin el fix,
`httpx.get("https://www.dof.gob.mx/sumarios/sumario_31dias.xml", verify=ctx)`
con el `SSLContext` de `truststore` solo (sin el archivo de intermedios) falla
con `SSLCertVerificationError`. Con el fix cargado, la misma petición y la
equivalente contra el JSON del Senado responden **200** con verificación TLS
completa (no `verify=False`, no bypass).

**Nota relacionada, fuera de alcance:** `sil.gobernacion.gob.mx` (Sistema de
Información Legislativa, ver sección de fuentes descartadas) tiene el mismo
tipo de cadena incompleta (`*.gobernacion.gob.mx`), pero como esa fuente ya
está descartada por obsoleta, no se agregó su intermedio.

## 2–6. Colectores nuevos (fase 4)

### CIADI (ICSID) — `app/sources/icsid.py`

Consume `https://icsid.worldbank.org/api/all/cases` (JSON), filtra casos
donde `claimant` o `respondent` contengan el radical normalizado "mexic"
(cubre "Mexico", "México" y "United Mexican States"). El endpoint no expone
`subject`/`econsector` (llegan vacíos para los 1149 casos, no solo los de
México) ni una fecha de actualización del caso, así que la novedad no puede
basarse en fecha:

- Se mantiene un snapshot local `{caseno: status}` (por defecto
  `data/icsid_snapshot.json`, agregado a `.gitignore`; configurable con la
  variable `RADAR_ICSID_SNAPSHOT` o el parámetro `snapshot_path`, para
  aislarlo en pruebas).
- Se emite un `Candidate` solo para casos **nuevos** (ausentes del snapshot
  previo) o con **cambio de estatus**. Sin cambios, no hay ruido diario.
- **Primera corrida** (sin snapshot): de los 56 casos históricos de México se
  emiten únicamente los `Pending` (21 en la corrida en vivo de esta
  auditoría), para no inundar el radar con 35 arbitrajes ya concluidos; se
  guarda igualmente el snapshot completo (`Pending` + `Concluded`) como línea
  base.
- `published_at` es la fecha de hoy en la zona configurada
  (`LOCAL_TIMEZONE`), porque el API no da fecha propia del caso: representa
  cuándo el radar detectó la novedad.

Comportamiento verificado en vivo y con pruebas: primera corrida con 3 casos
de muestra (1 Pending de México, 1 Concluded de México, 1 caso ajeno a
México) emite solo el Pending; una segunda corrida sin cambios emite 0; una
tercera corrida con un cambio de estatus y un caso nuevo emite exactamente
esos 2.

### Banco Mundial — `app/sources/worldbank.py`

Consume `https://search.worldbank.org/api/v2/news?format=json&count_exact=Mexico&rows=30&srt=lnchdt&order=desc`
(sin restricciones declaradas: `search.worldbank.org/robots.txt` devuelve 404).
`documents` es un diccionario keyed por id que mezcla, junto a los documentos
reales, una entrada de metadatos `"facets"` sin título ni url — se descarta
cualquier entrada sin `title`/`url` reconocibles. Título y descripción llegan
envueltos en `{"cdata!": "..."}` cuando el texto original traía CDATA (se
maneja también el caso de string plano). Fecha real desde `lnchdt` (ISO 8601
con sufijo `Z`).

### CPI (ICC) y CIJ (ICJ) — `app/sources/international.py`

Ambas son subclases de `RssCollector` (mismo parser tolerante que ONU/USTR/
Trade.gov y el DOF): bloques `<item>` aislados por regex + `ElementTree`, con
reintento sin prefijos de namespace.

- **CPI**: `https://www.icc-cpi.int/rss.xml`. El `robots.txt` del sitio
  (verificado en vivo) declara, para `User-agent: *` — el bucket que aplica a
  nuestro User-Agent declarado (`RadarRegulatorioMX/0.1 ...`, no listado
  explícitamente) — `Content-Signal: search=yes,ai-train=no,use=reference` y
  `Allow: /`. `use=reference` autoriza exactamente lo que hace este
  recolector: título, enlace y un resumen, nunca la reentrega íntegra del
  sitio. El bloqueo explícito (`Disallow: /`) que el mismo `robots.txt` lista
  para bots como Amazonbot, ClaudeBot o GPTBot no aplica a nuestro
  User-Agent.
- **CIJ**: `https://www.icj-cij.org/rss.xml`. `robots.txt` es el genérico de
  Drupal, sin restricciones por user-agent ni bloqueo de `/rss.xml`.

### Diputados: anexos de la Gaceta Parlamentaria — `app/sources/diputados.py`

El índice (`#Indice`) ya se recolectaba; se agregó la lectura del
`<div id="Anexos">` (confirmado en el markup real: el 7 de julio de 2026,
sin sesión nueva ese día, la página seguía mostrando la edición del lunes 6
de julio con dos anexos). Cada anexo es un enlace "Anexo N" a un PDF con
patrón `/PDF/<legislatura>/AAAA/mes/AAAAMMDD-*.pdf`, seguido en el mismo
`<p>` de una descripción breve. Se emite un `Candidate` con
`official_title="Anexo N de la Gaceta Parlamentaria (dictámenes y minutas
del Pleno)"`, `document_type="Dictámenes y minutas"`, `published_at` tomado
del nombre de archivo (no del título de la gaceta, porque un anexo puede
documentar una sesión de días previos, p. ej. de la Comisión Permanente) y
`url` absoluta. Verificado en vivo: ambos PDFs de la corrida de prueba
respondieron 200.

### PRODECON siempre-relevante — `app/sources/gobmx.py`

Los boletines de prensa de PRODECON (verificados en vivo: `Boletín 09/2026`,
`Tarjeta informativa`, etc.) tienen títulos genéricos que nunca pasan
`looks_relevant`, aunque el mandato entero de PRODECON (Procuraduría de la
Defensa del Contribuyente) es fiscal por definición. Se agregó
`ALWAYS_RELEVANT_PORTALS = {"prodecon"}`: el contenido de ese portal se
acepta sin pasar por el filtro de título.

Se evaluó si CONDUSEF y PROFECO necesitaban el mismo tratamiento, mirando sus
archivos de prensa reales en vivo. A diferencia de PRODECON, su contenido es
mayormente protección al consumidor genérica y ajena al alcance del radar:
alertas de suplantación de identidad bancaria, precios de alimentos, quejas
de aerolíneas, educación financiera sobre pagos sin contacto. Ninguno de los
títulos revisados en vivo (9 de cada portal) pasa el filtro de relevancia, y
a diferencia de PRODECON no hay razón estructural para que todo lo que
publican sea relevante para este radar. Se decidió **no** agregarlos a
`ALWAYS_RELEVANT_PORTALS`: hacerlo inundaría el radar con ruido de consumo
que no es su objeto.

## Fuentes evaluadas y descartadas (o diferidas) en esta auditoría

- **INDAUTOR**: `robots.txt` (verificado en vivo) declara
  `User-agent: * / Disallow: /` — bloqueo total. No se scrapea; su
  cobertura regulatoria (avisos y trámites de derechos de autor con
  relevancia normativa) se obtiene indirectamente vía DOF, que sí publica
  los acuerdos y avisos formales del instituto.
- **OMC (WTO)**: `robots.txt` (verificado en vivo) bloquea a todo
  user-agent salvo Googlebot (`User-agent: * / Disallow: *`, con
  `User-agent: Googlebot / Allow: *`). Existe un RSS técnico
  (`www.wto.org/library/rss/latest_news_e.xml`, enlazado desde su propia
  página de "RSS feeds gateway"), pero el propio `robots.txt` impide su uso
  por cualquier agente que no sea Googlebot. No se implementa.
- **OCDE (OECD)**: bloqueo de datacenter confirmado en vivo — las peticiones
  a `www.oecd.org` (p. ej. `/en/about/news.html`) devuelven 403 con un reto
  de Cloudflare ("Just a moment..."), aunque el propio `robots.txt` no
  restringe esas rutas. Queda pendiente para un entorno con IP residencial.
- **FMI (IMF)**: a diferencia de la OCDE, `www.imf.org` **no** está
  bloqueado por datacenter en esta auditoría (200 con contenido real en
  `/en/news`). Los endpoints RSS heredados que se probaron
  (`/en/News/rss?Language=ENG`, `/external/cntpst/rss/whatsnew.aspx`) están
  muertos o redirigen a una página HTML "RSS" sin contenido XML real. Queda
  pendiente identificar el feed o API vigente del FMI (posiblemente vía su
  API de datos SDMX) en una fase futura; no es un bloqueo de red sino falta
  de un endpoint confirmado.
- **ANAM (Agencia Nacional de Aduanas de México)**: `www.anam.gob.mx` no
  respondió en ningún intento durante esta auditoría (timeout / error interno
  de protocolo HTTP/2 en TLS; `http://` da 403). Se difiere hasta que el
  sitio esté disponible de forma consistente.
- **T-MEC / USMCA — docket de solución de controversias**: el sistema de
  radicación electrónica de los paneles (TAS eFiling) redirige a un flujo de
  autenticación Azure AD B2C (`itatasb2c.b2clogin.com`) — requiere inicio de
  sesión, no es de acceso público. La cobertura de novedades de T-MEC se
  mantiene vía USTR (`app/sources/international.py`) y Secretaría de
  Economía (dentro de `app/sources/gobmx.py`, portal `se`), que sí publican
  comunicados públicos sobre el tratado.
- **SIL (Sistema de Información Legislativa)**: portal obsoleto — sin API ni
  RSS, HTML dinámico recargado por `setInterval`/jQuery cada 3 minutos, con
  rastreo de Google Analytics clásico (`ga.js`/`_gat._getTracker`)
  descontinuado hace más de una década, señal de que el frontend no se
  actualiza. Su contenido (iniciativas y proceso legislativo) ya está
  cubierto por la Gaceta Parlamentaria de Diputados y el JSON del Senado, con
  interfaces estructuradas y vigentes. No se implementa.

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

## Decisiones heredadas (fases 1–3, siguen vigentes)

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
  únicamente cuando el título contiene señales jurídicas relevantes (o el
  portal está en `ALWAYS_RELEVANT_PORTALS`, ver arriba).
- Los tres recolectores internacionales de la primera ola (ONU, USTR,
  Trade.gov) comparten `RssCollector`; sus feeds se confirmaron en vivo en
  esta auditoría (antes solo se habían probado contra fixtures, por bloqueo
  de red del entorno de esa sesión). Trade.gov es la excepción: su feed
  quedó obsoleto (ver tabla maestra).

## Fuentes previstas para siguientes fases

- Novedades normativas y técnicas del SAT.
- Gaceta de la Propiedad Industrial y documentos descargables del IMPI.
- Anteproyectos y consultas públicas regulatorias de autoridades federales.
- Jurisprudencia y comunicados relevantes del TFJA y la SCJN.
- OCDE y FMI, una vez resueltos el bloqueo de datacenter (OCDE) y la
  identificación de un endpoint vigente (FMI) — ver sección de fuentes
  diferidas arriba.
- Arreglo de Wassenaar (listas de control de exportación de doble uso): sin
  API/RSS conocido; requiere documentar mecanismo de publicación antes de
  integrarlo.

Cada incorporación debe revisar primero API, RSS, sitemap o datos abiertos y
documentar límites de uso antes de implementar extracción HTML.
