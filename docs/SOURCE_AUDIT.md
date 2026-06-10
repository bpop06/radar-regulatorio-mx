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

## Fuentes previstas para siguientes fases

- Comunicados y criterios sustantivos de PRODECON.
- Novedades normativas y técnicas del SAT.
- Gaceta de la Propiedad Industrial y documentos descargables del IMPI.
- Anteproyectos y consultas públicas regulatorias de autoridades federales.
- Jurisprudencia y comunicados relevantes del TFJA y la SCJN.

Cada incorporación debe revisar primero API, RSS, sitemap o datos abiertos y
documentar límites de uso antes de implementar extracción HTML.

