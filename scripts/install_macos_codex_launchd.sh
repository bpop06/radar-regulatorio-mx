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
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"

echo "Installed $LABEL"
echo "Schedule: daily at $(printf '%02d:%02d' "$RUN_HOUR" "$RUN_MINUTE") local Mac time"
echo "Log: $LOG_DIR/codex-daily.log"
echo "Run now with: launchctl kickstart gui/$(id -u)/$LABEL"
