# Automatización diaria en Mac

GitHub Actions no está arrancando runners para este repositorio: incluso un workflow manual mínimo termina en `startup_failure` antes de crear jobs. Mientras esa limitación esté activa en GitHub, la actualización diaria debe correr desde la Mac.

## Instalar la tarea diaria

Desde la carpeta del repositorio en la Mac:

```bash
chmod +x scripts/install_macos_launchd.sh
scripts/install_macos_launchd.sh
```

Esto instala el LaunchAgent `com.bpop06.radar-regulatorio-mx` y ejecuta la recolección todos los días a las 14:17 hora local de la Mac.

## Variables opcionales

Si se usa OpenAI, guarda variables en `~/.radar-regulatorio-mx.env`:

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
```

## Qué hace la tarea

1. Crea `.venv` si no existe.
2. Instala el paquete local.
3. Ejecuta `app.cli collect`.
4. Valida `site/data/publications.json`.
5. Si el JSON cambió, hace commit `chore: refresh regulatory data` y `git push origin main`.

Los logs quedan en `logs/collect-daily.log`.

## Ejecutar manualmente

```bash
scripts/collect_daily.sh
```

## Desinstalar

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.bpop06.radar-regulatorio-mx.plist"
rm "$HOME/Library/LaunchAgents/com.bpop06.radar-regulatorio-mx.plist"
```
