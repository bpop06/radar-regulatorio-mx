#!/usr/bin/env bash
set -euo pipefail

# Deja la automatización diaria del Radar Regulatorio MX andando en una Mac
# con un solo comando. Idempotente: se puede volver a correr sin problema
# (por ejemplo, para cambiar el horario o tras actualizar el repo).
#
# Compatible con bash 3.2 (el bash que trae macOS de fábrica). Si tu shell
# por defecto es zsh, el shebang de arriba hace que el sistema lo ejecute con
# bash de todas formas siempre que lo invoques directamente:
#   scripts/mac_bootstrap.sh
# (no lo antepongas con `sh` ni lo cargues con `source`, eso saltaría el
# shebang y bash-3.2-isms como ${BASH_SOURCE[0]} fallarían bajo zsh/sh).
#
# Uso:
#   scripts/mac_bootstrap.sh              # instala y pregunta si correr ya
#   scripts/mac_bootstrap.sh --run-now     # instala y corre la primera vez sin preguntar
#   RADAR_RUN_HOUR=8 RADAR_RUN_MINUTE=0 scripts/mac_bootstrap.sh   # horario custom

RUN_NOW=false
for arg in "$@"; do
  case "$arg" in
    --run-now)
      RUN_NOW=true
      ;;
    -h | --help)
      awk '
        /^#!/ { next }
        /^#/ { started = 1; sub(/^# ?/, ""); print; next }
        started { exit }
      ' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      echo "error: argumento desconocido '$arg' (usa --run-now o --help)" >&2
      exit 1
      ;;
  esac
done

log() {
  echo "==> $1"
}

# --- Paso 1: sistema operativo -------------------------------------------

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "error: este instalador sólo está pensado para macOS (uname -s = $(uname -s))." >&2
  exit 1
fi

log "macOS detectado ($(sw_vers -productVersion 2>/dev/null || echo 'versión desconocida'))"

# --- Paso 2: git y python3 >= 3.12 ---------------------------------------

if ! command -v git >/dev/null 2>&1; then
  echo "error: no se encontró git. Instálalo con 'xcode-select --install' o 'brew install git'." >&2
  exit 1
fi

log "git disponible: $(git --version)"

PYTHON_BIN=""
for candidate in python3 python3.13 python3.12; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
      PYTHON_BIN="$(command -v "$candidate")"
      break
    fi
  fi
done

if [[ -z "$PYTHON_BIN" ]]; then
  echo "error: no se encontró Python 3.12 o superior (se probó python3, python3.13, python3.12)." >&2
  echo "Instálalo con: brew install python" >&2
  exit 1
fi

export PYTHON_BIN
log "Python adecuado: $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"

# --- Paso 3: Codex CLI -----------------------------------------------------

if ! command -v codex >/dev/null 2>&1; then
  log "codex no está en PATH"
  if command -v npm >/dev/null 2>&1; then
    log "instalando codex con npm install -g @openai/codex"
    npm install -g @openai/codex
  else
    echo "error: no se encontró npm para instalar codex automáticamente." >&2
    echo "Instálalo con: brew install --cask codex" >&2
    exit 1
  fi
fi

if ! command -v codex >/dev/null 2>&1; then
  echo "error: codex sigue sin encontrarse en PATH tras el intento de instalación." >&2
  echo "Instálalo manualmente con: brew install --cask codex" >&2
  exit 1
fi

log "codex disponible: $(codex --version)"

# --- Paso 4: sesión de Codex -----------------------------------------------

CODEX_AUTH_FILE="$HOME/.codex/auth.json"
if [[ ! -f "$CODEX_AUTH_FILE" ]]; then
  log "no hay sesión de codex guardada ($CODEX_AUTH_FILE); abriendo 'codex login'"
  codex login
  log "codex login terminó"
else
  log "sesión de codex ya existe en $CODEX_AUTH_FILE"
fi

# --- Paso 5: raíz del repo y actualización de main -------------------------

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
log "raíz del repositorio: $ROOT"

CURRENT_BRANCH="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")"
if [[ "$CURRENT_BRANCH" == "main" ]]; then
  log "rama actual es main; actualizando con git pull --ff-only origin main"
  if ! git -C "$ROOT" pull --ff-only origin main; then
    echo "warning: no se pudo actualizar main (sin red o historia divergente); continuando con el estado local" >&2
  fi
else
  echo "warning: la rama actual es '$CURRENT_BRANCH', no main; se omite el pull. Cambia a main manualmente si quieres actualizar." >&2
fi

# --- Paso 6: instalar la tarea programada ----------------------------------

log "instalando el LaunchAgent de codex (scripts/install_macos_codex_launchd.sh)"
chmod +x "$ROOT/scripts/install_macos_codex_launchd.sh" "$ROOT/scripts/codex_daily.sh" "$ROOT/scripts/collect_daily.sh"
"$ROOT/scripts/install_macos_codex_launchd.sh"

RUN_HOUR="${RADAR_RUN_HOUR:-9}"
RUN_MINUTE="${RADAR_RUN_MINUTE:-30}"

# --- Paso 7: primera corrida opcional ---------------------------------------

if [[ "$RUN_NOW" != true ]]; then
  if [[ -t 0 ]]; then
    read -r -p "¿Ejecutar la primera corrida ahora en primer plano? [S/n] " reply
    case "$reply" in
      [nN]*)
        RUN_NOW=false
        ;;
      *)
        RUN_NOW=true
        ;;
    esac
  else
    log "sin terminal interactiva; se omite la primera corrida (usa --run-now para forzarla)"
    RUN_NOW=false
  fi
fi

if [[ "$RUN_NOW" == true ]]; then
  log "corriendo scripts/codex_daily.sh en primer plano (puede tardar varios minutos)"
  "$ROOT/scripts/codex_daily.sh"
  log "corrida terminada; últimas líneas del parte diario del agente:"
  if [[ -s "$ROOT/logs/codex-last-message.txt" ]]; then
    tail -n 40 "$ROOT/logs/codex-last-message.txt"
  else
    echo "(sin parte diario disponible; revisa $ROOT/logs/codex-daily.log)"
  fi
fi

# --- Paso 8: resumen final ---------------------------------------------------

cat <<SUMMARY

==================================================================
Radar Regulatorio MX — instalación en la Mac lista
==================================================================
Horario programado: todos los días a las $(printf '%02d:%02d' "$RUN_HOUR" "$RUN_MINUTE") hora local de la Mac.
Logs:
  - $ROOT/logs/codex-daily.log        (corrida completa)
  - $ROOT/logs/codex-last-message.txt (parte diario del agente)

Ejecutar manualmente:
  scripts/codex_daily.sh      # con agente Codex (con su respaldo automático)
  scripts/collect_daily.sh    # recolector directo, sin agente

Probar el horario ya instalado sin esperar:
  launchctl kickstart "gui/\$(id -u)/com.bpop06.radar-regulatorio-mx.codex"

Desinstalar:
  launchctl bootout "gui/\$(id -u)" "\$HOME/Library/LaunchAgents/com.bpop06.radar-regulatorio-mx.codex.plist"
  rm "\$HOME/Library/LaunchAgents/com.bpop06.radar-regulatorio-mx.codex.plist"

Más detalle en docs/MAC_SCHEDULE.md.
SUMMARY
