# Automatización diaria en la Mac

GitHub Actions de esta cuenta sigue fallando con `startup_failure` antes de
crear jobs, así que la actualización diaria corre desde la Mac. Hay dos
modalidades; la recomendada es el **agente Codex**, que además de recolectar
investiga fuentes caídas y entrega un parte diario.

## Opción A (recomendada): agente Codex diario

> **Importante:** instala esto sólo cuando la skill ya esté integrada en
> `main` (verifica que `main` contenga `.agents/skills/radar-diario/SKILL.md`
> y `scripts/codex_daily.sh`). La tarea diaria trabaja sobre `main`: si estos
> archivos siguen únicamente en una rama, la corrida hará checkout de un
> `main` sin skill y la mención `$radar-diario` no resolverá.

La skill vive en el repositorio, en `.agents/skills/radar-diario/SKILL.md`
(la ubicación estándar multi-agente que Codex escanea desde el directorio de
trabajo hasta la raíz del repo). Codex la detecta automáticamente al trabajar
dentro del repo; requiere Codex CLI 0.80 o posterior, donde las skills ya
vienen habilitadas sin configuración.

### Requisitos en la Mac (una sola vez)

1. Instalar Codex CLI:

   ```bash
   npm install -g @openai/codex   # o: brew install --cask codex
   ```

2. Iniciar sesión (queda guardada para las corridas sin supervisión):

   ```bash
   codex login
   ```

3. Clonar el repositorio (si aún no está) y entrar a su carpeta:

   ```bash
   git clone https://github.com/bpop06/radar-regulatorio-mx.git
   cd radar-regulatorio-mx
   ```

4. Verificar que la skill aparece: dentro de `codex`, escribir `$radar-diario`
   (o pedir "ejecuta el radar diario") y confirmar que la reconoce.

### Instalar la tarea diaria

```bash
chmod +x scripts/install_macos_codex_launchd.sh
scripts/install_macos_codex_launchd.sh
```

Esto instala el LaunchAgent `com.bpop06.radar-regulatorio-mx.codex`, que
ejecuta todos los días a las 09:30 (hora local de la Mac) el script
`scripts/codex_daily.sh`:

1. Actualiza `main` (`git pull --ff-only`).
2. Corre `codex exec` con la skill `$radar-diario` en sandbox
   `workspace-write` con red habilitada.
3. Si Codex no está instalado o su corrida falla, cae automáticamente al
   recolector directo (`scripts/collect_daily.sh`) para no perder el día.

Para cambiar el horario, reinstala con variables:

```bash
RADAR_RUN_HOUR=8 RADAR_RUN_MINUTE=0 scripts/install_macos_codex_launchd.sh
```

Para probarlo de inmediato sin esperar el horario:

```bash
launchctl kickstart "gui/$(id -u)/com.bpop06.radar-regulatorio-mx.codex"
```

Logs: `logs/codex-daily.log` (corrida completa) y
`logs/codex-last-message.txt` (parte diario del agente).

### Desinstalar

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.bpop06.radar-regulatorio-mx.codex.plist"
rm "$HOME/Library/LaunchAgents/com.bpop06.radar-regulatorio-mx.codex.plist"
```

## Opción B: recolector directo sin agente

Instala el LaunchAgent clásico que solo ejecuta el pipeline de Python:

```bash
chmod +x scripts/install_macos_launchd.sh
scripts/install_macos_launchd.sh
```

No instales las dos opciones a la vez: el instalador de Codex retira el
LaunchAgent clásico si lo encuentra, para evitar corridas dobles.

## Variables opcionales

Si se usa OpenAI para los resúmenes, guarda variables en
`~/.radar-regulatorio-mx.env`:

```bash
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.5
```

También se pueden configurar:

```bash
LOCAL_TIMEZONE=America/Mexico_City
REQUEST_TIMEOUT_SECONDS=45
SOURCE_RETRIES=3
SOURCE_RETRY_BACKOFF_SECONDS=1.5
LOOKBACK_DAYS=31
CODEX_MODEL=            # opcional: fija el modelo que usa codex exec
```

## Qué publica la tarea

En ambas opciones, si la recolección y la validación pasan y el JSON cambió,
se hace commit `chore: refresh regulatory data` de
`docs/data/publications.json` y `git push origin main`. Como GitHub Pages
sirve la carpeta `docs/` de `main`, ese push actualiza la página publicada.

## Ejecutar manualmente

```bash
scripts/codex_daily.sh      # con agente Codex
scripts/collect_daily.sh    # recolector directo
```

## Solución de problemas conocidos

- **La corrida de Codex no produce salida y termina sin error.** Algunas
  versiones (0.124–0.125) tienen una regresión de `codex exec` sin terminal.
  Revisa `codex --version` y actualiza (`npm update -g @openai/codex`). El
  script cae al recolector directo, así que el día no se pierde.
- **`codex exec` se queda colgado.** Suele ser stdin abierto sin escritor;
  `scripts/codex_daily.sh` ya redirige `</dev/null` para evitarlo.
- **El agente no tiene red dentro del sandbox.** El sandbox
  `workspace-write` bloquea red por defecto; el script la habilita con
  `-c sandbox_workspace_write.network_access=true`. Si corres Codex a mano,
  agrega ese override.
- **Falla de autenticación en corridas sin supervisión.** La sesión de
  `codex login` queda en `~/.codex/auth.json`; ese archivo debe existir y ser
  escribible (Codex refresca el token y lo reescribe). Basta hacer
  `codex login` una vez en la Mac con el navegador.
- **`codex: command not found` desde launchd.** launchd arranca con PATH
  mínimo; el script ya antepone `/opt/homebrew/bin` y `/usr/local/bin`. Si tu
  instalación vive en otra ruta, define `CODEX_BIN=/ruta/al/codex` en
  `~/.radar-regulatorio-mx.env`.
