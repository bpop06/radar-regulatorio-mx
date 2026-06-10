# Radar Regulatorio MX

Sitio diario de inteligencia regulatoria mexicana. Reúne publicaciones
oficiales, detecta asuntos relevantes y presenta un título ejecutivo con un
resumen de exactamente 30 palabras.

## Fuentes del MVP

- Diario Oficial de la Federación
- SNICE
- PLATIICA
- Gaceta Parlamentaria de la Cámara de Diputados
- Datos públicos de iniciativas del Senado
- Instituto Mexicano de la Propiedad Industrial

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

El workflow `Collect and deploy` se ejecuta diariamente a las 20:17 UTC
(14:17 de Ciudad de México), consulta las fuentes y despliega el sitio en
GitHub Pages. La clave de OpenAI, si se usa, debe guardarse como GitHub Actions
Secret.

## Git

Consulta [`docs/GIT_WORKFLOW.md`](docs/GIT_WORKFLOW.md) para ramas, commits,
integración y manejo de secretos.
