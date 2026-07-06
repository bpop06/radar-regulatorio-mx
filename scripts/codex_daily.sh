#!/usr/bin/env bash
set -euo pipefail

# Ejecuta la actualización diaria del radar mediante el agente Codex CLI.
# Pensado para correr desde launchd en la Mac (docs/MAC_SCHEDULE.md).
# Si Codex no está disponible, cae al recolector directo (collect_daily.sh)
# para que la publicación diaria nunca dependa del agente.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/logs"
LOCK_DIR="$ROOT/.codex-daily.lock"
ENV_FILE="${RADAR_ENV_FILE:-$HOME/.radar-regulatorio-mx.env}"
CODEX_BIN="${CODEX_BIN:-codex}"
CODEX_MODEL="${CODEX_MODEL:-}"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

mkdir -p "$LOG_DIR"
exec >>"$LOG_DIR/codex-daily.log" 2>&1

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] starting codex daily run"

STALE_LOCK_MINUTES=360

# Un lock huérfano (proceso muerto por SIGKILL o apagón) no debe bloquear
# corridas futuras para siempre. `find -mmin +N` es portable a bash 3.2 de
# macOS (no depende de features de bash 4).
if [[ -d "$LOCK_DIR" ]] && [[ -n "$(find "$LOCK_DIR" -maxdepth 0 -mmin +$STALE_LOCK_MINUTES 2>/dev/null)" ]]; then
  echo "lock dir $LOCK_DIR is older than $STALE_LOCK_MINUTES minutes; treating as stale and removing"
  rmdir "$LOCK_DIR" 2>/dev/null || rm -rf "$LOCK_DIR"
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "another codex run is already in progress"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

cd "$ROOT"

# El repositorio debe partir de main actualizado para que el agente trabaje
# sobre la última versión publicada. Un fallo aquí (sin red, árbol sucio) no
# debe matar la corrida: se continúa con el estado local y se deja constancia.
if ! { git fetch origin main && git checkout main && git pull --ff-only origin main; }; then
  echo "warning: could not update main; continuing with current checkout"
fi

LAST_MESSAGE_FILE="$LOG_DIR/codex-last-message.txt"

# Corre el recolector directo tras descartar residuos que el agente pudiera
# haber dejado en el archivo de datos, para que parta de un estado conocido.
# Reutilizada tanto si `codex exec` sale con error como si sale 0 sin
# producir salida (ver más abajo).
run_fallback() {
  echo "$1"
  git checkout -- docs/data/publications.json 2>/dev/null || true
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "warning: working tree has leftover changes from the codex run"
  fi
  "$ROOT/scripts/collect_daily.sh"
}

if ! command -v "$CODEX_BIN" >/dev/null 2>&1; then
  echo "codex CLI not found; falling back to direct collection"
  "$ROOT/scripts/collect_daily.sh"
  exit $?
fi

# La mención $radar-diario invoca explícitamente la skill del repo. Se
# incluye la ruta literal como respaldo por si la mención no resuelve en la
# versión instalada de Codex. Comillas simples: el $ es de Codex, no del
# shell.
PROMPT='$radar-diario Ejecuta la actualización diaria completa del Radar Regulatorio MX siguiendo en orden los pasos de la skill definida en .agents/skills/radar-diario/SKILL.md y termina con el parte diario.'

# workspace-write no da red por defecto; el override -c la habilita porque
# la recolección y git push la necesitan. stdin se redirige a /dev/null:
# codex exec se cuelga esperando EOF si stdin queda como pipe abierto.
CODEX_ARGS=(
  exec
  --cd "$ROOT"
  --sandbox workspace-write
  -c 'sandbox_workspace_write.network_access=true'
  --output-last-message "$LAST_MESSAGE_FILE"
)

if [[ -n "$CODEX_MODEL" ]]; then
  CODEX_ARGS+=(--model "$CODEX_MODEL")
fi

# Se borra antes de invocar codex para poder distinguir, tras una corrida que
# sale 0, entre "el agente terminó y escribió su parte" y la regresión
# conocida de codex exec (versiones 0.124-0.125): sale con éxito sin producir
# ninguna salida.
rm -f "$LAST_MESSAGE_FILE"

if ! "$CODEX_BIN" "${CODEX_ARGS[@]}" "$PROMPT" </dev/null; then
  run_fallback "codex run failed; falling back to direct collection"
elif [[ ! -s "$LAST_MESSAGE_FILE" ]]; then
  run_fallback "codex exited 0 but $LAST_MESSAGE_FILE is missing or empty (known codex exec regression); falling back to direct collection"
fi

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] codex daily run completed"
