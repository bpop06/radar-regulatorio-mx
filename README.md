# Radar Regulatorio MX

Sitio diario de inteligencia regulatoria mexicana. Reúne publicaciones
oficiales, detecta asuntos relevantes y presenta un título editorial (órgano +
acción + sustancia), un resumen de entre 40 y 80 palabras y una ficha con
jerarquía fija (qué se publicó —sin número de acto ni nombre completo del
órgano—, sustancia y fuente oficial).

Cada novedad queda clasificada por dependencia u órgano emisor, rama de
gobierno, jurisdicción (nacional/internacional), fecha (facetas de año, mes y
día), materias, etiquetas temáticas e importancia editorial (1-5). El contrato
público completo es `docs/data/publications.json`; la portada consume además
`docs/data/edition.json`, un artefacto ligero con el corte del día, cobertura y
un máximo de siete señales priorizadas. Cada archivo se publica mediante
reemplazo atómico y el historial de git funciona como archivo del radar (un
corte por commit); la base SQLite (`research`) es opcional para trabajo local.

## Materias cubiertas

Cada publicación se agrupa en una **materia primaria** (`categories`) y
conserva sus **etiquetas finas** en `topic_tags`. Las materias primarias son:
Fiscal, Comercio exterior, Comercio internacional, Penal, Anti-lavado,
Administración centralizada, Administración descentralizada y Proceso
legislativo. Las etiquetas finas que las alimentan:

- Fiscal (incluye el contencioso administrativo fiscal, entendido como la
  especialidad fiscal dentro del contencioso, no cualquier acto de
  recaudación o ejecución).
- Aduanero y comercio exterior (importación, exportación, aranceles, cuotas
  compensatorias, reglas generales de comercio exterior).
- Comercio internacional: T-MEC/USMCA, OMC, OCDE, arbitraje de inversión
  (CIADI/ICSID), paneles y solución de diferencias.
- Penal: delitos, defraudación fiscal, contrabando, extinción de dominio y la
  justicia penal internacional (CPI).
- Anti-lavado: LFPIORPI, actividades vulnerables, UIF, GAFI y beneficiario
  controlador.
- Propiedad intelectual y normalización (colapsan a Administración
  centralizada o descentralizada según la rama del órgano emisor).
- Derecho administrativo federal: LFPA, LOAPF, reglamentos interiores,
  estructura orgánica, facultades y organización de toda la Administración
  Pública Federal.
- Nombramientos federales: designaciones, ratificaciones, remociones,
  suplencias y encargadurías de mandos y titulares.
- Contencioso administrativo general.
- Iniciativas legislativas sobre cualquiera de estas materias (materia
  primaria Proceso legislativo).

## Fuentes del MVP

- Diario Oficial de la Federación
- SNICE
- PLATIICA
- Gaceta Parlamentaria de la Cámara de Diputados
- Datos públicos de iniciativas del Senado
- Instituto Mexicano de la Propiedad Industrial
- Portales institucionales de la Administración Pública Federal publicados en
  el índice oficial de `gob.mx`
- Internacionales (primera ola): ONU Noticias, USTR y Trade.gov, con catálogo
  de fase 2 (OCDE, OMC, FMI, Banco Mundial, CIADI, Wassenaar, CPI, CIJ) en la
  auditoría de fuentes

La auditoría técnica está en [`docs/SOURCE_AUDIT.md`](docs/SOURCE_AUDIT.md).

## Desarrollo local

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements-dev.txt
python -m pip install -e . --no-deps
python -m app.cli research --output docs/data/publications.json
python -m app.cli build-edition
python -m http.server 8000 --directory docs
```

Abrir `http://localhost:8000`.

### Sistema visual

La interfaz se comporta como una mesa editorial de cartas físicas. Las señales
de la portada forman una baraja navegable por clic, toque o teclado; seleccionar
una carta la adelanta, cambia el pigmento de la materia y actualiza la noticia
principal. El archivo usa un muro asimétrico de cartas y la ficha superpone la
evidencia sobre el expediente.

Los materiales de papel y acuarela viven en `docs/assets/`; las tipografías
Fraunces, Alegreya Sans e IBM Plex Mono se sirven localmente desde `docs/fonts/`
con sus licencias OFL. El movimiento evita animar layout, se limita a
`transform` y `opacity`, y respeta `prefers-reduced-motion`.

Comandos del CLI: `research` (recolecta, guarda la corrida en la base local y
publica ambos artefactos), `collect` (sin base), `export-site` (regenera los
artefactos desde la última corrida guardada), `build-edition` (migra el
contrato y reconstruye únicamente la edición), `storage-report` (tamaño y
contenido de la base) y `validate`.

La recolección local genera resúmenes extractivos deterministas
(`ai_generated: false`). La capa editorial — título de noticia, resumen de 40
a 80 palabras y cuerpo de la ficha — la produce la rutina diaria de Claude en
la nube ([`docs/EDITORIAL_CLOUD.md`](docs/EDITORIAL_CLOUD.md)), que aplica sus
textos con `python -m app.cli apply-editorial` y marca `ai_generated: true`.
No se usa ninguna API de pago: todo corre con la suscripción de Claude.

## Automatización

Todo el ciclo diario corre en la nube con la rutina de Claude (11:00 CDMX):
recolecta las 10 fuentes oficiales con el pipeline determinista, redacta la
capa editorial con razonamiento opus, audita el corte con la guía
[`radar-diario`](.agents/skills/radar-diario/SKILL.md) y publica sólo
`docs/data/publications.json` y `docs/data/edition.json`; ver
[`docs/EDITORIAL_CLOUD.md`](docs/EDITORIAL_CLOUD.md). Nada se procesa ni se
guarda en máquinas locales; el historial de git es el archivo del radar.
Codex y la API de OpenAI quedaron fuera del ciclo.

GitHub Actions de esta cuenta falla con `startup_failure` antes de crear jobs,
por lo que los workflows quedan sólo como respaldo manual mientras eso se
resuelve con GitHub.

## Publicación en GitHub Pages

El sitio es la carpeta [`docs/`](docs/) y se sirve con GitHub Pages en modo
"Deploy from a branch". Esto no depende de los workflows del repositorio (los
de `.github/workflows/`, que hoy fallan con `startup_failure`), sino del
proceso interno de Pages que gestiona GitHub, el workflow "pages build and
deployment" — que también corre sobre Actions, pero es distinto y separado de
los workflows del repo. Activación (una sola vez, desde la web de GitHub):

1. El repositorio debe ser público para usar Pages en el plan gratuito
   (Settings → General → Danger Zone → Change visibility), o bien la cuenta
   debe tener GitHub Pro para publicarlo desde un repo privado.
2. Settings → Pages → Build and deployment → Source: **Deploy from a branch**.
3. Branch: **main**, carpeta **/docs** → Save.

La página queda en `https://bpop06.github.io/radar-regulatorio-mx/` y se
actualiza sola con cada push a `main` que toque `docs/` (es lo que hace la
rutina diaria al publicar `docs/data/publications.json`).

Si tras activar Pages el sitio no publica y el workflow interno "pages build
and deployment" también falla, la causa es el bloqueo de Actions a nivel de
cuenta (facturación o verificación pendiente) y hay que resolverlo con
soporte de GitHub, no en este repositorio.

## Memoria local y servidor dedicado

La base histórica (`data/radar.sqlite3`) vive solo en la máquina que corre la
recolección. Órdenes de magnitud: ~3 KB por publicación enriquecida en el JSON;
en SQLite, alrededor de 100–250 MB al llegar a 10,000 documentos con índices y
1–3 GB hacia 100,000. Para uso individual sin textos completos, la Mac es
suficiente durante años (decenas de MB por año). Conviene evaluar un servidor
dedicado cuando se quiera: archivo documental completo (PDF/sentencias, >20 GB),
búsqueda multiusuario o embeddings, más de ~50 fuentes con corridas
garantizadas aunque la Mac esté apagada, o colas de procesamiento LLM con
monitoreo de costos. `python -m app.cli storage-report` da las cifras reales
para decidir el momento.

## Git

Consulta [`docs/GIT_WORKFLOW.md`](docs/GIT_WORKFLOW.md) para ramas, commits,
integración y manejo de secretos.
