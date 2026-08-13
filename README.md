# Radar Regulatorio MX

Servicio de inteligencia regulatoria que consulta 18 fuentes oficiales,
conserva evidencia estable y publica un corte dos veces al día. GitHub Pages
sirve `main/docs`.

El sitio distingue entre una ficha editorial **completa** y una publicación
**pendiente de revisión**. Cuando la fuente no contiene sustancia suficiente,
el registro conserva su título oficial, fecha, órgano y enlace, sin fabricar
una interpretación.

## Fuentes certificadas para el relanzamiento

DOF, SNICE, PLATIICA, Cámara de Diputados, Senado, IMPI, gob.mx APF, ONU
Noticias, USTR, Trade.gov, CIADI, ANAM, TFJA, OMC, Secretariado T-MEC, Banco
Mundial, CPI y CIJ.

Una fuente aislada puede quedar `degraded` sin bloquear a las demás. El
relanzamiento inicial exige certificar las 18; un fallo total conserva el corte
anterior.

## Contrato público v8

Cada documento tiene identidad estable (`source_id` y `canonical_url`),
hash de contenido, primera y última detección, fecha oficial separada de la
fecha de detección y estado editorial explícito.

Artefactos:

- `docs/data/manifest.json`: `cut_id`, versión y hashes del corte.
- `docs/data/edition.json`: portada ligera y cobertura.
- `docs/data/publications.json`: compatibilidad durante la migración.
- `docs/data/archive/YYYY-MM.json`: índices mensuales permanentes.
- `docs/data/items/<hash>.json`: evidencia y ficha estable por documento.
- `docs/notas/<hash>.html`: nota estática con metadatos sociales.
- `docs/data/state/{icsid,tmec,retractions}.json`: estado confirmado con el
  mismo corte; `retractions` existe sólo cuando hay retiros vigentes.

La generación ocurre en staging. Sólo después de validar contrato, referencias
y hashes se reemplazan todos los artefactos; `--dry-run` no escribe ni consume
estado.

## Desarrollo

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
.venv/bin/python -m pip install -e . --no-deps
npm ci
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
npm test
npm run test:e2e
.venv/bin/python -m http.server 8000 --directory docs
```

Recolección sombra, sin publicar:

```bash
.venv/bin/python -m app.cli collect --days 31 --dry-run
```

Reconstrucción histórica one-shot de CIADI/T-MEC para el relanzamiento (aun si
ya existen snapshots), sin emitir el inventario como novedades del día:

```bash
.venv/bin/python -m app.cli collect --days 31 --rebuild-stateful-history
```

Esta bandera no se usa en la automatización diaria. Véase el runbook para el
ensayo con `--dry-run` y los gates posteriores.

Aplicación editorial validada:

```bash
.venv/bin/python -m app.cli apply-editorial edits.json \
  --input docs/data/publications.json
```

La interpretación editorial la realiza Codex a partir de fuentes oficiales.
Python no llama APIs de modelos.

Retiro transaccional de una ficha que no constituye una novedad regulatoria:

```bash
.venv/bin/python -m app.cli retract-items SOURCE:ID \
  --reason "Motivo verificable" --input docs/data/publications.json
.venv/bin/python -m app.cli restore-items SOURCE:ID \
  --input docs/data/publications.json
```

El retiro elimina ficha, nota e índices en el mismo corte y conserva motivo y
respaldo en el estado autenticado por el manifiesto para permitir rollback.

## Operación

Una sola automatización Codex ejecuta a las 10:30 y 18:30, hora de Ciudad de
México, incluidos fines de semana. Trabaja en un worktree limpio, mantiene un
único PR `codex/radar-daily`, espera los checks y verifica que Pages publique
el mismo `cut_id`. No hay pushes directos a `main`.

El runbook, los gates de relanzamiento, el SLA y la recuperación están en
[`docs/OPERATIONS.md`](docs/OPERATIONS.md). El flujo Git está en
[`docs/GIT_WORKFLOW.md`](docs/GIT_WORKFLOW.md) y la certificación técnica de
fuentes en [`docs/SOURCE_AUDIT.md`](docs/SOURCE_AUDIT.md).
