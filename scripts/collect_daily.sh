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
"$PY" -m pip install -e .

export LOCAL_TIMEZONE="${LOCAL_TIMEZONE:-America/Mexico_City}"
export REQUEST_TIMEOUT_SECONDS="${REQUEST_TIMEOUT_SECONDS:-45}"
export SOURCE_RETRIES="${SOURCE_RETRIES:-3}"
export SOURCE_RETRY_BACKOFF_SECONDS="${SOURCE_RETRY_BACKOFF_SECONDS:-1.5}"

"$PY" -m app.cli collect --output site/data/publications.json --days "$LOOKBACK_DAYS"
"$PY" -m app.cli validate --input site/data/publications.json

if git diff --quiet -- site/data/publications.json; then
  echo "no publication data changes"
  exit 0
fi

if ! git config user.name >/dev/null; then
  git config user.name "bpop06"
fi

if ! git config user.email >/dev/null; then
  git config user.email "140567647+bpop06@users.noreply.github.com"
fi

git add site/data/publications.json
git commit -m "chore: refresh regulatory data"
git push origin main

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] collection completed"
