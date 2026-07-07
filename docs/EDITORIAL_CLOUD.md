# Rutina editorial diaria en la nube (Claude)

La capa editorial del radar la produce una **rutina diaria de Claude** que
corre en la nube con la suscripción del propietario — sin API de OpenAI y sin
Codex.

## Qué hace cada día (11:00 CDMX / 17:00 UTC)

1. Abre una sesión nueva de Claude Code en el entorno del repositorio y trae
   `main` actualizado (la Mac ya recolectó y publicó datos extractivos a las
   9:30).
2. Lee `docs/data/publications.json` y selecciona los ítems con
   `ai_generated: false` (pendientes de editorial).
3. Redacta por lotes, con subagentes de razonamiento (opus), para cada ítem:
   - **Título de noticia**: «órgano + verbo sustantivo + qué cambia», regido
     por la temática esencial; el número de oficio/acuerdo jamás abre el
     título.
   - **Resumen de exactamente 30 palabras**, autosuficiente y sin inventar
     hechos (solo a partir del título oficial, la descripción y la autoridad).
   - **`card_body`** con la jerarquía fija `## Qué se publicó`,
     `## Sustancia`, `## Fuente`.
4. Aplica las ediciones con el único canal permitido:
   `python -m app.cli apply-editorial ediciones.json` — el comando rechaza
   ids inexistentes, resúmenes ≠ 30 palabras, cuerpos sin las tres secciones
   y títulos que inicien con número de oficio; es todo-o-nada y termina con
   la validación completa del contrato.
5. Audita el corte siguiendo `.agents/skills/radar-diario/SKILL.md`
   (solo lectura).
6. Commitea **únicamente** `docs/data/publications.json` con el mensaje
   `chore: editorial diario` y empuja a `main` (con reintentos). GitHub Pages
   publica.
7. Termina con el parte diario, que llega como **notificación push**.

## Operación de la rutina

La rutina es un *trigger* de Claude Code (`create_new_session_on_fire`) con
cron `0 17 * * *`. Desde cualquier sesión de Claude Code conectada a la
cuenta se administra con las herramientas del servidor Claude_Code_Remote:

- `list_triggers` — ver el trigger y su próximo disparo.
- `update_trigger` — pausar (`enabled: false`), reanudar o cambiar horario.
- `fire_trigger` — dispararla de inmediato fuera de horario.
- `delete_trigger` — eliminarla.

También se puede pedir en lenguaje natural a Claude: «pausa la rutina
editorial del radar», «dispárala ahora», etc.

## Límites de seguridad

- La rutina solo puede modificar los tres campos editoriales de ítems
  existentes a través de `apply-editorial`; el resto del contrato y del
  repositorio no es escribible por ese canal.
- Si un día la Mac no publicó, la rutina editorializa el corte previo y lo
  anota en el parte; no recolecta (no tiene red hacia sitios de gobierno) ni
  inventa novedades.
