# Radar Regulatorio MX

Sitio diario de inteligencia regulatoria mexicana. Reúne publicaciones
oficiales, detecta asuntos relevantes y presenta un título ejecutivo con un
resumen de exactamente 30 palabras.

## Materias cubiertas

- Fiscal, aduanero y comercio exterior.
- Propiedad intelectual y normalización.
- Derecho administrativo federal: LFPA, LOAPF, reglamentos interiores,
  estructura orgánica, facultades y organización de toda la Administración
  Pública Federal.
- Nombramientos federales: designaciones, ratificaciones, remociones,
  suplencias y encargadurías de mandos y titulares.
- Contencioso administrativo general.
- Contencioso administrativo fiscal, entendido como la especialidad fiscal
  dentro del contencioso administrativo, no como cualquier acto de
  recaudación o ejecución.
- Iniciativas legislativas sobre cualquiera de estas materias.

## Fuentes del MVP

- Diario Oficial de la Federación
- SNICE
- PLATIICA
- Gaceta Parlamentaria de la Cámara de Diputados
- Datos públicos de iniciativas del Senado
- Instituto Mexicano de la Propiedad Industrial
- Portales institucionales de la Administración Pública Federal publicados en
  el índice oficial de `gob.mx`

La auditoría técnica está en [`docs/SOURCE_AUDIT.md`](docs/SOURCE_AUDIT.md).

## Desarrollo local

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m app.cli collect --output docs/data/publications.json
python -m http.server 8000 --directory docs
```

Abrir `http://localhost:8000`.

Sin `OPENAI_API_KEY`, el sistema genera un resumen extractivo de respaldo. Con
la variable configurada utiliza OpenAI Responses API y siempre normaliza el
resultado a 30 palabras.

## Automatización

La actualización diaria corre desde la Mac con launchd mediante el agente
Codex: la skill [`radar-diario`](.agents/skills/radar-diario/SKILL.md) recolecta
las fuentes, valida, investiga incidencias y publica. La instalación en la Mac
y la modalidad sin agente están en [`docs/MAC_SCHEDULE.md`](docs/MAC_SCHEDULE.md).

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
tarea diaria de la Mac al publicar `docs/data/publications.json`).

Si tras activar Pages el sitio no publica y el workflow interno "pages build
and deployment" también falla, la causa es el bloqueo de Actions a nivel de
cuenta (facturación o verificación pendiente) y hay que resolverlo con
soporte de GitHub, no en este repositorio.

## Git

Consulta [`docs/GIT_WORKFLOW.md`](docs/GIT_WORKFLOW.md) para ramas, commits,
integración y manejo de secretos.
