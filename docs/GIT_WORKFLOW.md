# Flujo de trabajo con Git

## Contrato GitHub Free

Este proyecto opera en un **repositorio público con GitHub Free**. La
configuración deliberadamente se limita a funciones incluidas sin costo:

- GitHub Actions usa únicamente runners estándar `ubuntu-latest`. En
  repositorios públicos su uso es gratuito e ilimitado. No se configuran
  larger runners, runner groups, imágenes personalizadas ni runners con GPU.
- GitHub Pages publica mediante **Deploy from a branch**, rama `main`, carpeta
  `/docs`. No hay un pipeline de hosting de pago ni un workflow de despliegue
  alterno.
- `main` usa una regla clásica de protección de rama, disponible para
  repositorios públicos con GitHub Free, y exige los checks `Python` y
  `Frontend`.
- El repositorio permite auto-merge, también disponible para repositorios
  públicos con GitHub Free. Auto-merge sólo integra el PR cuando la protección
  de rama ya está satisfecha.
- No se habilita **Require merge queue** ni se declara el evento
  `merge_group`; una cola de merge no es necesaria para este repositorio.

El script `scripts/verify_github_free.sh` hace fallar CI si un workflow cambia
de runner, activa `merge_group`, deja de exigir el manifiesto o recupera el
fallback a schema v7.

Referencias oficiales: [runners alojados por GitHub][runners], [ramas
protegidas][protected], [auto-merge][automerge] y [disponibilidad de
Pages][pages].

[runners]: https://docs.github.com/actions/reference/runners/github-hosted-runners
[protected]: https://docs.github.com/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
[automerge]: https://docs.github.com/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-auto-merge-for-pull-requests-in-your-repository
[pages]: https://docs.github.com/pages/getting-started-with-github-pages/github-pages-limits

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

### Configuración única del repositorio

1. En **Settings → Pages**, seleccionar **Deploy from a branch**, `main` y
   `/docs`.
2. Dejar que `Python` y `Frontend` terminen correctamente al menos una vez para
   que GitHub los ofrezca como checks seleccionables.
3. En **Settings → Branches**, crear una regla clásica para `main`; activar
   **Require a pull request before merging** y **Require status checks to pass
   before merging**, exigir que la rama esté actualizada, seleccionar
   exactamente `Python` y `Frontend` y activar **Do not allow bypassing the
   above settings**. No exigir aprobaciones: este repositorio tiene un solo
   operador y la garantía la dan los dos checks.
4. No activar **Require merge queue**. En **Settings → General → Pull
   Requests**, activar **Allow auto-merge**.
5. En cada PR diario, habilitar auto-merge. Si uno de los dos checks no inicia,
   queda pendiente o falla, el PR permanece abierto.

Ningún script, workflow o agente puede empujar directamente a `main`. Si
Actions no inicia o falla, el PR queda abierto y producción conserva el corte
anterior.

## Seguridad y recuperación

- No versionar `.env`, bases SQLite, credenciales ni descargas masivas.
- Mantener permisos mínimos y acciones fijadas por SHA.
- No reescribir la historia de `main`; revertir mediante un nuevo PR.
