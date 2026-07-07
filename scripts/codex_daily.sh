#!/usr/bin/env bash
set -euo pipefail

# Corrida diaria del Radar Regulatorio MX (launchd en la Mac; ver
# docs/MAC_SCHEDULE.md). Conserva su nombre histórico para que el
# LaunchAgent instalado siga funcionando sin reinstalar.
#
# Arquitectura vigente: la Mac SOLO recolecta y publica datos extractivos de
# forma determinista (collect_daily.sh commitea únicamente
# docs/data/publications.json). La capa editorial y la auditoría las hace la
# rutina diaria de Claude en la nube (docs/EDITORIAL_CLOUD.md) — aquí ya no
# corre ningún agente ni se usa ninguna API.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/logs"
LOCK_DIR="$ROOT/.codex-daily.lock"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

mkdir -p "$LOG_DIR"
exec >>"$LOG_DIR/codex-daily.log" 2>&1

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] starting daily run"

STALE_LOCK_MINUTES=360

# Un lock huérfano (proceso muerto por SIGKILL o apagón) no debe bloquear
# corridas futuras para siempre. `find -mmin +N` es portable a bash 3.2 de
# macOS (no depende de features de bash 4).
if [[ -d "$LOCK_DIR" ]] && [[ -n "$(find "$LOCK_DIR" -maxdepth 0 -mmin +$STALE_LOCK_MINUTES 2>/dev/null)" ]]; then
  echo "lock dir $LOCK_DIR is older than $STALE_LOCK_MINUTES minutes; treating as stale and removing"
  rmdir "$LOCK_DIR" 2>/dev/null || rm -rf "$LOCK_DIR"
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "another daily run is already in progress"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

cd "$ROOT"

# Partir de main actualizado. Un fallo aquí (sin red, árbol sucio) no debe
# matar la corrida: se continúa con el estado local y se deja constancia.
if ! { git fetch origin main && git checkout main && git pull --ff-only origin main; }; then
  echo "warning: could not update main; continuing with current checkout"
fi

publish_status=0
"$ROOT/scripts/collect_daily.sh" || publish_status=$?
if [[ "$publish_status" -ne 0 ]]; then
  echo "warning: la recolección determinista terminó con código $publish_status"
fi

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] daily run completed"
exit "$publish_status"
