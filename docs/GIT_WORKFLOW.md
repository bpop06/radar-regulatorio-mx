# Flujo de trabajo con Git

## Ramas

- `main`: versión estable y desplegable.
- `codex/feature-*`: funcionalidades nuevas.
- `codex/fix-*`: correcciones.
- `codex/docs-*`: documentación.

Las ramas deben durar poco y enfocarse en un solo objetivo.

## Commits

Usar mensajes imperativos y Conventional Commits:

```text
feat: add DOF collector
fix: normalize PLATIICA links
test: cover relevance classifier
docs: explain daily workflow
```

No mezclar refactorizaciones, datos generados y cambios funcionales sin
relación en el mismo commit.

## Integración

1. Crear una rama desde `main`.
2. Implementar y probar localmente.
3. Revisar el diff y confirmar que no contiene secretos.
4. Abrir un pull request.
5. Esperar las verificaciones automáticas. Mientras GitHub Actions de esta
   cuenta siga en `startup_failure` (ver README), esas verificaciones no
   corren en el PR: hay que ejecutarlas en local antes del merge
   (`.venv/bin/python -m pytest`, `.venv/bin/python -m ruff check .`,
   `.venv/bin/python -m app.cli validate --input docs/data/publications.json`).
6. Integrar con squash merge y borrar la rama.

## Seguridad

- Los secretos de workflows viven en GitHub Actions Secrets; la operación
  diaria no usa APIs de pago ni credenciales adicionales (la configuración
  local opcional vive en `~/.radar-regulatorio-mx.env`, fuera del repo).
- No versionar `.env`, bases SQLite locales ni credenciales.
- Mantener permisos mínimos para workflows.
- Fijar versiones principales de las acciones de GitHub y revisar
  periódicamente sus actualizaciones.

## Recuperación

No reescribir la historia de `main`. Para deshacer un cambio publicado, crear
un commit de reversión mediante `git revert`.
