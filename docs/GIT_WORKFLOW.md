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
5. Esperar las verificaciones automáticas.
6. Integrar con squash merge y borrar la rama.

## Seguridad

- Guardar `OPENAI_API_KEY` y otros secretos únicamente en GitHub Actions
  Secrets.
- No versionar `.env`, bases SQLite locales ni credenciales.
- Mantener permisos mínimos para workflows.
- Fijar versiones principales de las acciones de GitHub y revisar
  periódicamente sus actualizaciones.

## Recuperación

No reescribir la historia de `main`. Para deshacer un cambio publicado, crear
un commit de reversión mediante `git revert`.
