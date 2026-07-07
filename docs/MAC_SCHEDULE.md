# Automatización diaria en la Mac (RETIRADA)

> **Esta modalidad quedó retirada.** La recolección y la editorial corren
> ahora íntegramente en la nube con la rutina diaria de Claude
> ([`docs/EDITORIAL_CLOUD.md`](EDITORIAL_CLOUD.md)); la Mac ya no guarda ni
> procesa nada localmente. Si tenías instalada la tarea, desinstálala:
>
> ```bash
> launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.bpop06.radar-regulatorio-mx.codex.plist" 2>/dev/null
> rm -f "$HOME/Library/LaunchAgents/com.bpop06.radar-regulatorio-mx.codex.plist"
> launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.bpop06.radar-regulatorio-mx.plist" 2>/dev/null
> rm -f "$HOME/Library/LaunchAgents/com.bpop06.radar-regulatorio-mx.plist"
> ```
>
> Los scripts de `scripts/` se conservan solo como respaldo manual de
> emergencia (una corrida puntual desde cualquier máquina con red).

La Mac tiene UNA responsabilidad: **recolectar y publicar los datos** todos
los días de forma determinista. La capa editorial y la auditoría corren en la
nube con la rutina diaria de Claude ([`docs/EDITORIAL_CLOUD.md`](EDITORIAL_CLOUD.md)).
Aquí ya no interviene Codex ni ninguna API.

GitHub Actions de esta cuenta sigue fallando con `startup_failure`, por eso
la recolección corre desde la Mac con launchd.

## Arranque en un paso

```bash
git clone https://github.com/bpop06/radar-regulatorio-mx.git
cd radar-regulatorio-mx
scripts/mac_bootstrap.sh            # instala la tarea diaria (09:30)
scripts/mac_bootstrap.sh --run-now  # ídem + primera corrida inmediata
```

Requisitos: macOS con git y Python 3.12+ (`brew install python`). Es
idempotente: re-ejecútalo tras un `git pull` o para cambiar el horario con
`RADAR_RUN_HOUR`/`RADAR_RUN_MINUTE`.

## Qué hace la tarea diaria (09:30 hora local)

El LaunchAgent `com.bpop06.radar-regulatorio-mx.codex` (conserva su nombre
histórico; ya no ejecuta Codex) corre `scripts/codex_daily.sh`:

1. Actualiza `main` (`git pull --ff-only`).
2. Corre `scripts/collect_daily.sh`: crea/actualiza el venv desde los locks
   con hashes, ejecuta `app.cli research` (recolecta las fuentes, valida,
   escribe `docs/data/publications.json` y acumula la corrida en la base
   histórica local `data/radar.sqlite3`, ignorada por git) y, si el JSON
   cambió, commitea **únicamente** ese archivo (`chore: refresh regulatory
   data`) y empuja a `main`.
3. GitHub Pages publica; a las 11:00 CDMX la rutina de Claude editorializa.

Logs: `logs/codex-daily.log` y `logs/collect-daily.log`.

## Variables opcionales (`~/.radar-regulatorio-mx.env`)

```bash
LOCAL_TIMEZONE=America/Mexico_City
REQUEST_TIMEOUT_SECONDS=45
SOURCE_RETRIES=3
SOURCE_RETRY_BACKOFF_SECONDS=1.5
LOOKBACK_DAYS=31
```

## Operación manual

```bash
scripts/codex_daily.sh                       # corrida diaria completa
.venv/bin/python -m app.cli storage-report   # tamaño de la base histórica
launchctl kickstart "gui/$(id -u)/com.bpop06.radar-regulatorio-mx.codex"
```

## Desinstalar

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.bpop06.radar-regulatorio-mx.codex.plist"
rm "$HOME/Library/LaunchAgents/com.bpop06.radar-regulatorio-mx.codex.plist"
```

## Solución de problemas

- **La corrida no publica.** Revisa `logs/collect-daily.log`: si todas las
  fuentes fallaron (sin red), `research` sale con error y no escribe nada; el
  JSON del día anterior sigue publicado.
- **Lock atorado.** Los locks (`.codex-daily.lock`, `.collect-daily.lock`) se
  autolimpian si tienen más de 6 horas (apagones/SIGKILL).
- **Push rechazado.** `collect_daily.sh` rebasa (`git pull --rebase`) antes de
  empujar y aborta limpio si hay conflicto; reintenta en la siguiente corrida.
