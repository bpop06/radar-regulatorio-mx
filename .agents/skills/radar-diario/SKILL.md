---
name: radar-diario
description: >
  Ejecuta la actualización diaria del Radar Regulatorio MX: recolecta las
  fuentes oficiales mexicanas (DOF, SNICE, PLATIICA, Diputados, Senado, IMPI y
  portales de gob.mx), valida los datos, investiga fuentes caídas o novedades
  destacadas, publica el JSON en GitHub Pages y entrega un parte diario. Úsala
  cuando pidan actualizar el radar, correr la recolección diaria o investigar
  novedades regulatorias en dependencias de gobierno.
---

# Radar diario

Eres el operador diario del Radar Regulatorio MX. Tu objetivo es dejar
publicada la actualización del día en `docs/data/publications.json` (la
carpeta `docs/` es el sitio de GitHub Pages) y reportar qué encontraste.
Trabaja siempre desde la raíz del repositorio y respeta `AGENTS.md`.

## Paso 1 — Preparar el entorno

1. Confirma que estás en `main` actualizado: `git checkout main` y
   `git pull --ff-only origin main`. Si hay cambios locales sin commitear que
   no creaste tú, detente y repórtalo en el resumen final sin tocarlos.
2. Asegura el entorno virtual:
   - Si no existe `.venv`, créalo con `python3 -m venv .venv` (requiere
     Python 3.12 o superior; en la Mac suele ser el `python3` de Homebrew).
   - Instala dependencias: `.venv/bin/python -m pip install -e ".[dev]"`.
3. Variables opcionales: si existe `~/.radar-regulatorio-mx.env`, expórtalas
   antes de recolectar (`set -a; source ~/.radar-regulatorio-mx.env; set +a`).
   `OPENAI_API_KEY` activa resúmenes con IA; sin ella hay resumen extractivo.

## Paso 2 — Recolectar y validar

1. Ejecuta la recolección:
   `.venv/bin/python -m app.cli collect --output docs/data/publications.json --days 31`
2. Ejecuta la validación:
   `.venv/bin/python -m app.cli validate --input docs/data/publications.json`
3. Reglas duras:
   - Si la validación falla, NO publiques. Restaura el archivo con
     `git checkout -- docs/data/publications.json`, diagnostica y repórtalo.
   - Si todas las fuentes fallan (sin red, por ejemplo), el comando `collect`
     termina con error y no escribe el archivo: no fuerces nada, repórtalo.

## Paso 3 — Investigar como agente (tu valor agregado)

1. Revisa el estado de fuentes en la salida de `collect` (stderr) o en la
   clave `sources` del JSON generado.
2. Para cada fuente con `status: error` que siga fallando tras los
   reintentos automáticos:
   - Distingue entre caída temporal del sitio (timeout, 5xx) y cambio de
     estructura (parser roto, 404 en la ruta conocida).
   - Si el sitio cambió de estructura, intenta una corrección mínima del
     recolector correspondiente en `app/sources/`, con su prueba en `tests/`,
     en una rama `codex/fix-<fuente>`; corre `python -m pytest` y
     `python -m ruff check .`. Empuja la rama y déjala lista para revisión.
     Nunca mezcles esa corrección con el commit de datos ni la subas a `main`.
3. Haz un control de calidad muestral de las novedades del día: abre 2 o 3
   ítems nuevos y verifica que el enlace oficial responde, que el resumen
   tiene exactamente 30 palabras y que la clasificación de materias es
   razonable. Anota cualquier anomalía para el resumen final.

## Paso 4 — Publicar

Solo si la validación pasó y hay cambios en los datos:

```bash
git add docs/data/publications.json
git commit -m "chore: refresh regulatory data"
git push origin main
```

- No incluyas en ese commit ningún otro archivo (ni código, ni logs, ni
  `.venv`, ni secretos).
- Si `git push` falla por red, reintenta hasta 4 veces con espera creciente
  (2, 4, 8 y 16 segundos).
- Si no hubo cambios en el JSON, no crees commits vacíos: repórtalo.
- GitHub Pages sirve `docs/` directamente desde `main`, así que el push es la
  publicación: no hay paso extra de despliegue.

## Paso 5 — Parte diario

Termina siempre con un resumen en este formato:

```
RADAR DIARIO — <fecha local America/Mexico_City>
Fuentes: <n> ok, <n> con error (<nombres con error>)
Novedades publicadas: <total_items> (antes: <total anterior>)
Destacados: <2 a 4 líneas con los temas más relevantes del día,
citando materia y autoridad>
Incidencias: <parsers rotos, ramas codex/fix-* creadas, anomalías de
calidad, o "ninguna">
Publicación: <hash del commit y confirmación del push, o motivo por el
que no se publicó>
```

## Límites

- Nunca reescribas la historia de `main` ni hagas `push --force`.
- Nunca commitees claves, tokens, bases locales ni documentos descargados.
- No modifiques el diseño del sitio (`docs/*.html`, `docs/*.css`,
  `docs/*.js`) durante la corrida diaria; eso se hace por ramas de feature.
- Si algo impide completar la corrida, deja el repositorio limpio
  (`git status` sin restos tuyos) y explica el bloqueo en el parte diario.
