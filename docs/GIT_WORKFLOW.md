# Flujo de trabajo con Git

## Ramas y commits

- `main` es desplegable y GitHub Pages sirve `main/docs`.
- El desarrollo usa ramas cortas `codex/*`.
- Los cortes recurrentes reutilizan `codex/radar-daily` y un único PR.
- Los mensajes son imperativos y siguen Conventional Commits.
- Cuando sea práctico, los cambios de código y los datos generados van en
  commits distintos.

## Integración

1. Partir del `main` remoto en un checkout limpio.
2. Ejecutar los gates locales.
3. Revisar que el diff no contenga secretos ni artefactos ajenos.
4. Abrir o actualizar un pull request.
5. Esperar los checks requeridos `Python` y `Frontend`.
6. Habilitar auto-merge sólo cuando ambos estén verdes.
7. Verificar el `cut_id` y los hashes desplegados por Pages.

Ningún script, workflow o agente puede empujar directamente a `main`. Si
Actions no inicia o falla, el PR queda abierto y producción conserva el corte
anterior.

## Seguridad y recuperación

- No versionar `.env`, bases SQLite, credenciales ni descargas masivas.
- Mantener permisos mínimos y acciones fijadas por SHA.
- No reescribir la historia de `main`; revertir mediante un nuevo PR.
