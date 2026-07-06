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

1. Revisa `git status` ANTES de tocar ramas. Si hay cambios locales sin
   commitear que no creaste tú, detente y repórtalos en el parte diario sin
   tocarlos ni cambiar de rama.
2. Con el árbol limpio, confirma que estás en `main` actualizado:
   `git checkout main` y `git pull --ff-only origin main`. Si el pull falla
   (sin red, rama divergente), continúa con el estado local y anótalo.
3. Asegura el entorno virtual:
   - Verifica la versión: `python3 --version` debe ser 3.12 o superior. Si
     no lo es, usa un binario explícito (`python3.12`, `python3.13` o el de
     Homebrew) en el paso siguiente.
   - Si no existe `.venv`, créalo con `python3 -m venv .venv` (o con el
     binario que encontraste arriba).
   - Instala dependencias: `.venv/bin/python -m pip install -e ".[dev]"`.
4. Variables opcionales: si existe `~/.radar-regulatorio-mx.env`, expórtalas
   antes de recolectar (`set -a; source ~/.radar-regulatorio-mx.env; set +a`).
   `OPENAI_API_KEY` activa resúmenes con IA; sin ella hay resumen extractivo.

## Paso 2 — Recolectar y validar

1. Ejecuta la recolección:
   `.venv/bin/python -m app.cli collect --output docs/data/publications.json --days 31`
2. Sólo si `collect` terminó con éxito, corre la validación independiente:
   `.venv/bin/python -m app.cli validate --input docs/data/publications.json`
3. Reglas duras:
   - Si todas las fuentes fallan (sin red, por ejemplo), `collect` termina
     con error y NO escribe el archivo: el JSON en disco sigue siendo el del
     día anterior. No fuerces nada y no corras `validate` (pasaría en verde
     sobre datos viejos, una señal engañosa); salta al Paso 3 y repórtalo.
   - Si la validación falla, NO publiques. Diagnostica y repórtalo; deja el
     archivo tal cual para no perder evidencia.

## Paso 3 — Investigar como agente (tu valor agregado)

1. Revisa el estado de fuentes en la salida de `collect` (stderr). La clave
   `sources` del JSON sólo refleja esta corrida si `collect` escribió el
   archivo.
2. Para cada fuente con `status: error` que siga fallando tras los
   reintentos automáticos:
   - Distingue entre caída temporal del sitio (timeout, 5xx) y cambio de
     estructura (parser roto, 404 en la ruta conocida).
   - Si el sitio cambió de estructura, revisa primero si ya existe una rama
     `codex/fix-<fuente>` (local o remota); si existe, continúa en ella en
     lugar de crear otra. Intenta una corrección mínima del recolector en
     `app/sources/`, con su prueba en `tests/`; corre
     `.venv/bin/python -m pytest` y `.venv/bin/python -m ruff check .`.
     Empuja la rama y déjala lista para revisión. Nunca mezcles esa
     corrección con el commit de datos ni la subas a `main`.
3. Haz un control de calidad muestral: toma 2 o 3 ítems nuevos del día (o del
   corte vigente si hoy no hubo novedades) y verifica que el enlace oficial
   responde, que el resumen tiene exactamente 30 palabras y que la
   clasificación de materias es razonable. Si no hay red para verificar
   enlaces, haz la revisión estructural y anótalo como limitación. Registra
   cualquier anomalía para el parte diario.

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
  (2, 4, 8 y 16 segundos). Si lo rechazan por non-fast-forward, haz
  `git pull --rebase origin main` y vuelve a intentar una vez.
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
