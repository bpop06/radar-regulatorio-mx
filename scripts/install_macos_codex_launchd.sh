#!/usr/bin/env bash
set -euo pipefail

# Instala el LaunchAgent que ejecuta el agente Codex todos los días en la Mac.
# Sustituye a la tarea de recolección directa: si el LaunchAgent anterior
# (com.bpop06.radar-regulatorio-mx) está instalado, lo retira para evitar
# ejecuciones dobles. Ver docs/MAC_SCHEDULE.md.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.bpop06.radar-regulatorio-mx.codex"
LEGACY_LABEL="com.bpop06.radar-regulatorio-mx"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LEGACY_PLIST="$HOME/Library/LaunchAgents/$LEGACY_LABEL.plist"
LOG_DIR="$ROOT/logs"
RUN_HOUR="${RADAR_RUN_HOUR:-9}"
RUN_MINUTE="${RADAR_RUN_MINUTE:-30}"

# Validar como dígitos y normalizar a base 10 ANTES de comparar rangos: con
# cero a la izquierda (08, 09) la aritmética de bash y printf los tratarían
# como octal inválido.
if ! [[ "$RUN_HOUR" =~ ^[0-9]+$ ]]; then
  echo "error: RADAR_RUN_HOUR must be an integer between 0 and 23 (got '$RUN_HOUR')" >&2
  exit 1
fi
RUN_HOUR=$((10#$RUN_HOUR))
if [[ "$RUN_HOUR" -gt 23 ]]; then
  echo "error: RADAR_RUN_HOUR must be an integer between 0 and 23 (got '$RUN_HOUR')" >&2
  exit 1
fi

if ! [[ "$RUN_MINUTE" =~ ^[0-9]+$ ]]; then
  echo "error: RADAR_RUN_MINUTE must be an integer between 0 and 59 (got '$RUN_MINUTE')" >&2
  exit 1
fi
RUN_MINUTE=$((10#$RUN_MINUTE))
if [[ "$RUN_MINUTE" -gt 59 ]]; then
  echo "error: RADAR_RUN_MINUTE must be an integer between 0 and 59 (got '$RUN_MINUTE')" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

if [[ -f "$LEGACY_PLIST" ]]; then
  echo "Removing legacy LaunchAgent $LEGACY_LABEL to avoid duplicate runs"
  launchctl bootout "gui/$(id -u)" "$LEGACY_PLIST" >/dev/null 2>&1 || true
  rm "$LEGACY_PLIST"
fi

cat >"$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$ROOT/scripts/codex_daily.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$ROOT</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>$RUN_HOUR</integer>
    <key>Minute</key>
    <integer>$RUN_MINUTE</integer>
  </dict>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>HOME</key>
    <string>$HOME</string>
  </dict>
  <key>StandardInPath</key>
  <string>/dev/null</string>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/launchd.codex.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/launchd.codex.err.log</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
PLIST

chmod +x "$ROOT/scripts/codex_daily.sh" "$ROOT/scripts/collect_daily.sh"

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true

# Hay una race conocida entre bootout y bootstrap ("Bootstrap failed: 5:
# Input/output error") si launchd todavía no terminó de liberar el label
# anterior. Se reintenta unas cuantas veces con una pausa breve.
BOOTSTRAP_OK=false
for attempt in 1 2 3; do
  if launchctl bootstrap "gui/$(id -u)" "$PLIST"; then
    BOOTSTRAP_OK=true
    break
  fi
  echo "launchctl bootstrap failed (attempt $attempt/3); retrying in 2s"
  sleep 2
done

if [[ "$BOOTSTRAP_OK" != true ]]; then
  echo "error: launchctl bootstrap failed after 3 attempts" >&2
  exit 1
fi

launchctl enable "gui/$(id -u)/$LABEL"

echo "Installed $LABEL"
echo "Schedule: daily at $(printf '%02d:%02d' "$RUN_HOUR" "$RUN_MINUTE") local Mac time"
echo "Log: $LOG_DIR/codex-daily.log"
echo "Run now with: launchctl kickstart gui/$(id -u)/$LABEL"
