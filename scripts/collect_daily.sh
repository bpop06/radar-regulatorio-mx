#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/logs"
LOCK_DIR="$ROOT/.collect-daily.lock"
ENV_FILE="${RADAR_ENV_FILE:-$HOME/.radar-regulatorio-mx.env}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LOOKBACK_DAYS="${LOOKBACK_DAYS:-31}"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

mkdir -p "$LOG_DIR"
exec >>"$LOG_DIR/collect-daily.log" 2>&1

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] starting daily collection"

STALE_LOCK_MINUTES=360

# Un lock huérfano (proceso muerto por SIGKILL o apagón) no debe bloquear
# corridas futuras para siempre. `find -mmin +N` es portable a bash 3.2 de
# macOS (no depende de features de bash 4).
if [[ -d "$LOCK_DIR" ]] && [[ -n "$(find "$LOCK_DIR" -maxdepth 0 -mmin +$STALE_LOCK_MINUTES 2>/dev/null)" ]]; then
  echo "lock dir $LOCK_DIR is older than $STALE_LOCK_MINUTES minutes; treating as stale and removing"
  rmdir "$LOCK_DIR" 2>/dev/null || rm -rf "$LOCK_DIR"
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "another collection is already running"
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

if [[ ! -d ".venv" ]]; then
  "$PYTHON_BIN" -m venv .venv
fi

PY="$ROOT/.venv/bin/python"
"$PY" -m pip install --upgrade pip
# Dependencias fijadas con hashes (ver requirements.txt); el paquete local
# no lleva hash, por lo que se instala aparte con --no-deps.
"$PY" -m pip install --require-hashes -r requirements.txt
"$PY" -m pip install -e . --no-deps

export LOCAL_TIMEZONE="${LOCAL_TIMEZONE:-America/Mexico_City}"
export REQUEST_TIMEOUT_SECONDS="${REQUEST_TIMEOUT_SECONDS:-45}"
export SOURCE_RETRIES="${SOURCE_RETRIES:-3}"
export SOURCE_RETRY_BACKOFF_SECONDS="${SOURCE_RETRY_BACKOFF_SECONDS:-1.5}"

"$PY" -m app.cli collect --output docs/data/publications.json --days "$LOOKBACK_DAYS"
"$PY" -m app.cli validate --input docs/data/publications.json

if git diff --quiet -- docs/data/publications.json; then
  echo "no publication data changes"
  exit 0
fi

# Este script puede correr como respaldo justo después de que codex haya
# dejado el repo en una rama codex/fix-*. Nunca commitear ni empujar datos
# fuera de main.
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CURRENT_BRANCH" != "main" ]]; then
  echo "current branch is '$CURRENT_BRANCH', not main; attempting git checkout main"
  git checkout main 2>&1 || true
  CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
fi
if [[ "$CURRENT_BRANCH" != "main" ]]; then
  echo "error: could not switch to main (still on '$CURRENT_BRANCH'); refusing to commit or push"
  exit 1
fi

if ! git config user.name >/dev/null; then
  git config user.name "bpop06"
fi

if ! git config user.email >/dev/null; then
  git config user.email "140567647+bpop06@users.noreply.github.com"
fi

git add docs/data/publications.json
git commit -m "chore: refresh regulatory data"

# Si el remoto avanzó desde el pull inicial, rebasar antes de empujar; un
# rebase fallido no debe dejar el repo a medio rebase para la corrida
# siguiente.
if ! git pull --rebase origin main; then
  git rebase --abort 2>/dev/null || true
  echo "error: could not rebase onto origin/main; leaving local commit unpushed"
  exit 1
fi
git push origin main

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] collection completed"
