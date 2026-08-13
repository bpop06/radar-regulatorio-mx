# Flujo de trabajo con Git

## Contrato de costo cero

El proyecto usa únicamente funciones gratuitas:

- repositorio público en GitHub Free;
- GitHub Pages desde `main/docs` mediante **Deploy from a branch**;
- pull requests, protección clásica de `main` y auto-merge;
- una automatización local de Codex para recolección, editorial y CI;
- estados de commit gratuitos publicados con la API de GitHub.

GitHub Actions no forma parte del camino crítico. Los dos workflows se
conservan como diagnósticos manuales sobre `ubuntu-latest`, sin eventos
`push`, `pull_request`, `schedule` ni `merge_group`. Nunca se usan larger
runners, merge queue, hosting de pago ni un plan GitHub Pro/Team.

`scripts/verify_github_free.sh` hace fallar el gate si un workflow vuelve a
ejecutarse automáticamente, cambia de runner, activa una merge queue o deja de
validar el contrato v8.

## Gates requeridos

La protección de `main` exige estos dos estados, creados por la automatización
Codex sobre el SHA exacto del PR:

- `Codex local / Python`
- `Codex local / Frontend`

`scripts/run_codex_ci.sh` exige el número del PR, comprueba por API que esté
abierto contra `main` y que su cabeza coincida con el `HEAD` local, ejecuta los
comandos fijos en un clon limpio y publica primero `pending`; sólo publica
`success` cuando termina todo el grupo.
Un comando fallido publica `failure` y deja el PR abierto.

El grupo Python comprende Ruff, pytest, recolección en seco, validación v8 con
frescura, calendarios y `git diff --check`. El grupo Frontend comprende
instalación reproducible con `npm ci`, unitarias JavaScript, instalación de
Chromium y Playwright/axe. Frontend no corre si Python falla.

Los estados son transparentes: su descripción dice que fueron ejecutados por
Codex local. No se reutilizan los nombres `Python` o `Frontend` de Actions.

## Ramas, PR y merge

- `main` siempre debe ser desplegable y nunca recibe pushes directos.
- El desarrollo usa ramas cortas `codex/*`.
- Los cortes reutilizan `codex/radar-daily` y un único PR abierto.
- Los mensajes siguen Conventional Commits.
- Los cambios de código y los datos generados se separan cuando sea práctico.

Secuencia de integración:

1. Clonar `origin/main` en un directorio temporal limpio.
2. Generar el corte y su editorial validada.
3. Crear o actualizar el PR único.
4. Verificar que el SHA local coincida con la cabeza remota del PR.
5. Ejecutar `scripts/run_codex_ci.sh` sobre ese SHA.
6. Confirmar por API que ambos estados requeridos son `success`.
7. Habilitar auto-merge; nunca hacer push directo ni usar bypass administrativo.
8. Tras el merge, verificar `cut_id`, hashes, ficha histórica y enlaces en Pages.

## Configuración del repositorio

`main` usa una regla clásica con:

- pull request obligatorio;
- rama actualizada antes del merge;
- estados requeridos `Codex local / Python` y
  `Codex local / Frontend`;
- resolución de conversaciones;
- sin force-push, borrado o bypass administrativo.

El repositorio permite auto-merge y elimina la rama después del merge. No usa
merge queue. Pages sirve `main` y `/docs`.

## Límite de confianza

La automatización Codex es la autoridad que publica estados. Opera desde un
prompt local fuera del repositorio, clona desde GitHub y ejecuta los comandos
fijos; una modificación de un PR no puede omitir un gate cambiando el prompt.
El token de `gh` permanece en el llavero local y nunca se escribe en el repo.

Si Codex no puede ejecutar un gate, publicar el estado o verificar el SHA, el
PR no se integra y producción conserva el corte anterior.

Referencias: [estados de commit][statuses], [protección de ramas][protected],
[auto-merge][automerge] y [GitHub Pages][pages].

[statuses]: https://docs.github.com/rest/commits/statuses
[protected]: https://docs.github.com/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
[automerge]: https://docs.github.com/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-auto-merge-for-pull-requests-in-your-repository
[pages]: https://docs.github.com/pages/getting-started-with-github-pages/github-pages-limits

## Seguridad y recuperación

- No versionar `.env`, bases SQLite, credenciales ni descargas masivas.
- No reescribir la historia de `main`; revertir mediante un nuevo PR.
- Un estado `failure`, ausente o asociado a otro SHA nunca autoriza merge.
- Una corrida fallida o `--dry-run` no consume snapshots ni cambia producción.
