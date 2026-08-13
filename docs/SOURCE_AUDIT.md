# Auditoría de fuentes oficiales

Certificación del contrato v8 realizada **en vivo el 12–13 de agosto de
2026** desde el entorno operativo. El relanzamiento exige que las 18 fuentes
enumeradas abajo estén presentes, sin errores ni advertencias, y con al menos
un endpoint validado. Resultado de la corrida de certificación: **18/18
certificadas**.

La certificación comprueba transporte, `Content-Type` y estructura antes de
aceptar candidatos. Un resultado de cero publicaciones sólo es válido después
de esas comprobaciones; HTML inesperado, XML/JSON incompleto, 403/404 o una
respuesta parcial producen `degraded` o `error`, nunca un falso `ok: 0`.

## Fuentes certificadas

| Fuente | Interfaz oficial certificada | Contrato específico |
|---|---|---|
| DOF | RSS `https://dof.gob.mx/sumarios/sumario_31dias.xml` | Se usa el host **sin `www`**. Requiere canal RSS y conserva la URL oficial de cada nota. |
| SNICE | HTML `https://www.snice.gob.mx/cs/avi/snice/home.html` | Requiere la estructura pública de avisos y documentos. |
| PLATIICA | REST `https://platiica.economia.gob.mx/wp-json/wp/v2/posts` | Requiere la lista JSON de publicaciones; normaliza a dominio público los enlaces devueltos con host interno. |
| Diputados | HTML `https://gaceta.diputados.gob.mx/gp_hoy.html` | Valida índice y anexos de la Gaceta; emite enlaces permanentes fechados cuando están disponibles. |
| Senado | JSON anual de `transparenciaparlamentaria.senado.gob.mx` | Requiere la lista estructurada de iniciativas y proposiciones. |
| IMPI | HTML `https://www.gob.mx/impi/archivo/prensa` | Usa el archivo oficial de IMPI en Gob.mx, con título, fecha y URL estables; no depende de la portada que respondía 403. |
| Gob.mx APF | Sitemap `https://www.gob.mx/sitemap-gobierno.xml` y archivos públicos por portal | Cada portal se valida de forma independiente. Fallos parciales quedan en `warnings` y degradan la fuente sin ocultar los portales que sí respondieron. |
| ONU Noticias | RSS `https://news.un.org/feed/subscribe/es/news/all/rss.xml` | Requiere canal RSS y fechas válidas. |
| USTR | RSS `https://ustr.gov/rss.xml` | Requiere canal RSS y fechas válidas. |
| Trade.gov | HTML `https://www.trade.gov/press-releases` | Sustituye el RSS obsoleto: valida el índice oficial de comunicados de la ITA y sus fechas. |
| CIADI | JSON `https://icsid.worldbank.org/api/all/cases` y fichas HTML `case-database/case-detail` | Filtra casos relacionados con México, enriquece desde la ficha oficial y conserva instrumento invocado —incluido CPTPP—, partes, materia y estatus. |
| ANAM | RSS `https://www.anam.gob.mx/sitemap.rss` | Requiere canal RSS; descarta páginas índice y conserva comunicados o boletines con URL oficial. |
| TFJA | HTML `https://www.tfja.gob.mx/acuerdos/acuerdos_{año}/` | Requiere la estructura anual de acuerdos y fechas oficiales. |
| OMC | RSS `https://www.wto.org/library/rss/latest_news_e.xml` | Consume sólo el canal de suscripción oficial; no rastrea el resto del sitio. |
| Secretariado T-MEC | Tablas HTML públicas de capítulos 10 y 31 en `can-mex-usa-sec.org` | Mapea columnas por encabezado, no por posición; valida el enum de estatus, separa composición del panel y conserva la URL de cada tabla/caso. |
| Banco Mundial | JSON `https://search.worldbank.org/api/v2/news` | Requiere `documents`; descarta metadatos sin título/URL y usa la fecha oficial `lnchdt`. |
| CPI | RSS `https://www.icc-cpi.int/rss.xml` | Requiere canal RSS; usa título, enlace, fecha y extracto como referencia. |
| CIJ | HTML `https://www.icj-cij.org/press-releases` | Sustituye `/rss.xml`, que devuelve 404; valida número, fecha, título y enlace oficial del comunicado. |

Los conteos de candidatos pueden ser cero aunque una fuente esté certificada.
La certificación se refiere a que la interfaz respondió y satisfizo su contrato,
no a que necesariamente hubo una novedad relevante en la ventana consultada.

## Contrato operativo v8

- Cada recolector está aislado y reporta `validated_endpoints`, `warnings`,
  `items_found` y el error, si existe.
- `certified` requiere HTTP satisfactorio, tipo de contenido permitido,
  estructura reconocida y `validated_endpoints >= 1`.
- Una advertencia parcial convierte el resultado en `degraded`. En particular,
  Gob.mx conserva lo obtenido de los portales sanos y expone los 403, cambios
  de markup o archivos fallidos; no los silencia.
- Una fuente degradada no impide publicar las demás después del relanzamiento.
  Sólo el fallo total conserva el corte anterior. Para el relanzamiento
  inicial, cualquier fuente degradada, ausente o con error bloquea el corte.
- La identidad pública se basa en `source + source_id`, con URL canónica como
  respaldo. Se preservan por separado `official_published_at` y `detected_at`.
- CIADI y T-MEC detectan novedades por estado y huella de contenido. Un
  bootstrap crea la línea base sin presentar el inventario histórico como
  novedades del día.
- Los recolectores de estado sólo proponen el siguiente snapshot. Los archivos
  `docs/data/state/icsid.json` y `docs/data/state/tmec.json` se sustituyen junto
  con los demás artefactos después de validar todo el corte; `--dry-run` y una
  corrida fallida nunca consumen el estado anterior.

## Casos internacionales

CIADI toma el inventario JSON únicamente como punto de partida. Cuando un caso
es nuevo o cambia, valida su ficha HTML oficial y extrae, sin inferencias,
número, partes, materia de la controversia, sector, instrumento invocado,
estatus y último desarrollo disponible. Así se evita volver a atribuir un caso
CPTPP a T-MEC/TLCAN.

El Secretariado T-MEC lee las tablas públicas de capítulos 10 y 31 por sus
encabezados semánticos. Las partes, el mecanismo, el instrumento, la fecha de
solicitud, el estatus, la composición del panel y la fecha de informe final se
mantienen en campos distintos. Un estatus no reconocido rompe el contrato en
vez de desplazar columnas o publicar panelistas como estatus.

## Respeto de publicación y robots

- El DOF se consulta mediante su sumario RSS. No se descarga masivamente
  `nota_detalle.php`; cada registro conserva el enlace oficial para consulta.
- La OMC se consulta sólo mediante el RSS que publica como canal de
  suscripción. No se rastrean otras rutas bloqueadas por su `robots.txt`.
- El `robots.txt` de la CPI permite el uso de referencia para el agente
  declarado y prohíbe entrenamiento. El radar conserva enlace, metadatos y un
  extracto; no reentrega el contenido completo.
- INDAUTOR mantiene `Disallow: /` para todo agente y no se scrapea. Sus actos
  normativos formales pueden aparecer por DOF.
- Los recolectores prefieren API, REST o RSS. El HTML sólo se usa cuando la
  fuente oficial vigente no ofrece una interfaz estructurada utilizable.

## Cobertura y ampliaciones

El alcance certificado cubre derecho fiscal, aduanero, comercio exterior,
propiedad intelectual, organización y procedimiento administrativo federal,
contencioso administrativo y su subconjunto fiscal. El nombre de una autoridad
no basta por sí solo para clasificar la materia.

SAT, SCJN y PRODECON directo quedan para una ampliación posterior con colector,
fixtures, contrato de cero válido y certificación en vivo propios. Banxico,
OCDE, FMI, INDAUTOR y otras fuentes no forman parte de las 18 que bloquean este
relanzamiento; añadirlas requerirá el mismo proceso de certificación y revisión
de límites de uso.
