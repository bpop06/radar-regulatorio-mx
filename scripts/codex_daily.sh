#!/usr/bin/env bash
set -euo pipefail

# Corrida diaria del Radar Regulatorio MX (para launchd en la Mac; ver
# docs/MAC_SCHEDULE.md).
#
# Endurecimiento de seguridad:
#   - La PUBLICACIÓN la hace SIEMPRE el recolector determinista
#     (collect_daily.sh), que commitea únicamente docs/data/publications.json.
#   - El agente Codex corre DESPUÉS y sólo para INVESTIGAR y redactar el parte
#     diario, en sandbox `read-only`: no puede escribir el repo ni hacer push.
#   - La OPENAI_API_KEY del resumen NO se pasa al proceso del agente.
# Así, una inyección de prompt desde una fuente comprometida no tiene camino a
# ejecutar código en la Mac ni a robar la API key.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/logs"
LOCK_DIR="$ROOT/.codex-daily.lock"
ENV_FILE="${RADAR_ENV_FILE:-$HOME/.radar-regulatorio-mx.env}"
CODEX_BIN="${CODEX_BIN:-codex}"
CODEX_MODEL="${CODEX_MODEL:-}"

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

# Se cargan variables opcionales (CODEX_MODEL, CODEX_BIN, LOOKBACK_DAYS y, si se
# usa, OPENAI_API_KEY). La API key sólo debe llegar al recolector de Python; más
# abajo se elimina del entorno del agente con `env -u`.
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

cd "$ROOT"

# Partir de main actualizado. Un fallo aquí (sin red, árbol sucio) no debe matar
# la corrida: se continúa con el estado local y se deja constancia.
if ! { git fetch origin main && git checkout main && git pull --ff-only origin main; }; then
  echo "warning: could not update main; continuing with current checkout"
fi

LAST_MESSAGE_FILE="$LOG_DIR/codex-last-message.txt"

# --- Paso 1: publicación determinista (fuente de verdad del día) -------------
# collect_daily.sh recolecta, valida y publica ÚNICAMENTE docs/data/publications.json.
# Es el único componente con permiso de commit/push y no depende del agente.
publish_status=0
"$ROOT/scripts/collect_daily.sh" || publish_status=$?
if [[ "$publish_status" -ne 0 ]]; then
  echo "warning: la recolección determinista terminó con código $publish_status"
fi

# --- Paso 2: investigación del agente (mejor esfuerzo, solo lectura) ---------
# El agente NO puede escribir el repo ni hacer push (sandbox read-only) y no ve
# la OPENAI_API_KEY (env -u). Revisa la actualización ya publicada y redacta el
# parte diario en LAST_MESSAGE_FILE. Que falle no afecta la publicación del día.
PROMPT='$radar-diario Revisa la actualización de hoy del Radar Regulatorio MX, que ya fue publicada, siguiendo la skill definida en .agents/skills/radar-diario/SKILL.md: verifica la calidad de los datos, señala fuentes con error o anomalías y redacta el parte diario. No modifiques archivos ni hagas commits o push.'

rm -f "$LAST_MESSAGE_FILE"

if command -v "$CODEX_BIN" >/dev/null 2>&1; then
  # stdin a /dev/null: codex exec se cuelga esperando EOF si stdin queda como
  # pipe abierto.
  CODEX_ARGS=(
    exec
    --cd "$ROOT"
    --sandbox read-only
    --output-last-message "$LAST_MESSAGE_FILE"
  )
  if [[ -n "$CODEX_MODEL" ]]; then
    CODEX_ARGS+=(--model "$CODEX_MODEL")
  fi
  # env -u OPENAI_API_KEY: la key del resumen no debe estar en el entorno del
  # agente. Codex se autentica con ~/.codex/auth.json, no con esa variable.
  if ! env -u OPENAI_API_KEY -u OPENAI_MODEL "$CODEX_BIN" "${CODEX_ARGS[@]}" "$PROMPT" </dev/null; then
    echo "warning: la investigación de Codex falló; la publicación del Paso 1 ya se hizo"
  fi
else
  echo "codex CLI no encontrado; se omite la investigación (la publicación del Paso 1 ya se hizo)"
fi

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] daily run completed"

# El código de salida refleja la PUBLICACIÓN (Paso 1), no la investigación.
exit "$publish_status"
