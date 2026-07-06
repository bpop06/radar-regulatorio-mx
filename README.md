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
python -m app.cli collect --output site/data/publications.json
python -m http.server 8000 --directory site
```

Abrir `http://localhost:8000`.

Sin `OPENAI_API_KEY`, el sistema genera un resumen extractivo de respaldo. Con
la variable configurada utiliza OpenAI Responses API y siempre normaliza el
resultado a 30 palabras.

## Automatización

La recolección diaria está preparada para ejecutarse desde la Mac con launchd,
porque GitHub Actions puede quedar bloqueado antes de arrancar runners en este
repositorio. Consulta [`docs/MAC_SCHEDULE.md`](docs/MAC_SCHEDULE.md).

Los workflows de GitHub quedan disponibles sólo para ejecución manual mientras
se resuelve la capacidad/configuración de Actions de la cuenta.

## Git

Consulta [`docs/GIT_WORKFLOW.md`](docs/GIT_WORKFLOW.md) para ramas, commits,
integración y manejo de secretos.
