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
