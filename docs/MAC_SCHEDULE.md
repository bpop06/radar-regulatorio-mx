# Automatización diaria en la Mac

GitHub Actions de esta cuenta sigue fallando con `startup_failure` antes de
crear jobs, así que la actualización diaria corre desde la Mac. Hay dos
modalidades; la recomendada es el **agente Codex**, que además de recolectar
investiga fuentes caídas y entrega un parte diario.

## Arranque en un paso

Para dejar todo instalado con un solo comando en una Mac nueva:

```bash
git clone https://github.com/bpop06/radar-regulatorio-mx.git
cd radar-regulatorio-mx
scripts/mac_bootstrap.sh
```

El script verifica requisitos (macOS, git, Python 3.12+, Codex CLI y sesión
de `codex login`), instala la tarea programada (Opción A de abajo) y, al
final, pregunta si quieres correr la primera actualización de inmediato en
primer plano. Para saltarte esa pregunta y correrla sin supervisión, agrega
`--run-now`:

```bash
scripts/mac_bootstrap.sh --run-now
```

Es idempotente: se puede volver a ejecutar cuando quieras (por ejemplo, tras
`git pull`, o para cambiar el horario con `RADAR_RUN_HOUR`/`RADAR_RUN_MINUTE`).
El resto de este documento explica en detalle qué hace cada pieza y cómo
operarlas por separado.

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
2. **Publica de forma determinista:** corre `scripts/collect_daily.sh`, que
   recolecta, valida y commitea **únicamente** `docs/data/publications.json` a
   `main`. Es el único componente con permiso de publicar y no depende del
   agente, así que el día se actualiza aunque Codex falle o no esté instalado.
3. **Investiga con el agente (solo lectura):** corre `codex exec` con la skill
   `$radar-diario` en sandbox `read-only` para auditar la actualización ya
   publicada y redactar el parte diario. El agente **no** puede escribir el
   repo ni hacer push, y la `OPENAI_API_KEY` no se le pasa (endurecimiento de
   seguridad; ver la nota más abajo).

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

Este instalador corre siempre a las **14:17** hora local de la Mac: es un
horario fijo en el plist y no lee `RADAR_RUN_HOUR` ni `RADAR_RUN_MINUTE`
(esas variables sólo las respeta `scripts/install_macos_codex_launchd.sh`,
Opción A). Para cambiar el horario de la Opción B hay que editar el script.

No instales las dos opciones a la vez: cada instalador retira el LaunchAgent
de la otra opción si lo encuentra, para evitar corridas dobles.

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

En ambas opciones publica **el recolector determinista** `collect_daily.sh`:
si la recolección y la validación pasan y el JSON cambió, hace commit
`chore: refresh regulatory data` de **únicamente** `docs/data/publications.json`
y `git push origin main`. Como GitHub Pages sirve la carpeta `docs/` de `main`,
ese push actualiza la página publicada. El agente Codex nunca publica.

## Modelo de seguridad de la corrida diaria

- El agente Codex corre en sandbox **`read-only`**: no puede escribir el repo
  ni ejecutar `git push`, aunque una fuente comprometida intente inyectarle
  instrucciones. Su producto es texto (el parte diario), no cambios en `main`.
- El único que commitea es `collect_daily.sh`, y sólo el archivo de datos.
- La `OPENAI_API_KEY` (si la usas para resúmenes) se pasa **sólo** al
  recolector de Python; se elimina del entorno del proceso de Codex con
  `env -u OPENAI_API_KEY`.
- Si necesitas que Codex proponga un fix de parser, lo hará **como texto** en
  el parte diario; tú lo aplicas después en una rama `codex/fix-<fuente>`.

## Ejecutar manualmente

```bash
scripts/codex_daily.sh      # publica (determinista) + investiga (Codex, solo lectura)
scripts/collect_daily.sh    # sólo publica, sin agente
```

## Solución de problemas conocidos

- **La corrida de Codex no produce salida o falla.** No afecta la publicación
  del día: `collect_daily.sh` ya publicó en el Paso 1 antes de invocar a Codex.
  El parte diario simplemente queda vacío o incompleto. Revisa
  `codex --version` y actualiza (`npm update -g @openai/codex`) si persiste.
- **`codex exec` se queda colgado.** Suele ser stdin abierto sin escritor;
  `scripts/codex_daily.sh` ya redirige `</dev/null` para evitarlo.
- **El agente sólo audita datos locales, no re-consulta las fuentes.** Es a
  propósito: corre en sandbox `read-only` sobre la actualización ya publicada,
  sin salida a la red. Eso elimina cualquier canal de exfiltración desde el
  agente. La verificación en vivo de fuentes la hace el recolector, no Codex.
- **Falla de autenticación en corridas sin supervisión.** La sesión de
  `codex login` queda en `~/.codex/auth.json`; ese archivo debe existir y ser
  escribible (Codex refresca el token y lo reescribe). Basta hacer
  `codex login` una vez en la Mac con el navegador.
- **`codex: command not found` desde launchd.** launchd arranca con PATH
  mínimo; el script ya antepone `/opt/homebrew/bin` y `/usr/local/bin`. Si tu
  instalación vive en otra ruta, define `CODEX_BIN=/ruta/al/codex` en
  `~/.radar-regulatorio-mx.env`.
