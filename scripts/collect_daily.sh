#!/usr/bin/env bash
set -euo pipefail

# Safe local helper for a cut branch. It never commits, pushes or switches
# branches. Production publication is owned by the Codex project automation
# and always goes through one pull request.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LOOKBACK_DAYS="${LOOKBACK_DAYS:-31}"
EXPECTED_BRANCH="${RADAR_DAILY_BRANCH:-codex/radar-daily}"

cd "$ROOT"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "error: the cut helper requires a clean worktree" >&2
  exit 1
fi

CURRENT_BRANCH="$(git branch --show-current)"
if [[ "$CURRENT_BRANCH" != "$EXPECTED_BRANCH" ]] && [[ "${RADAR_ALLOW_DEV_BRANCH:-0}" != "1" ]]; then
  echo "error: expected branch '$EXPECTED_BRANCH', found '$CURRENT_BRANCH'" >&2
  echo "set RADAR_ALLOW_DEV_BRANCH=1 only for an intentional shadow run" >&2
  exit 1
fi

if [[ ! -x ".venv/bin/python" ]]; then
  "$PYTHON_BIN" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
  .venv/bin/python -m pip install -e . --no-deps
fi

export LOCAL_TIMEZONE="${LOCAL_TIMEZONE:-America/Mexico_City}"
export REQUEST_TIMEOUT_SECONDS="${REQUEST_TIMEOUT_SECONDS:-45}"
export SOURCE_RETRIES="${SOURCE_RETRIES:-3}"
export SOURCE_RETRY_BACKOFF_SECONDS="${SOURCE_RETRY_BACKOFF_SECONDS:-1.5}"

if [[ "${RADAR_REQUIRE_RELAUNCH_CERTIFICATION:-0}" == "1" ]]; then
  .venv/bin/python -m app.cli certify-sources --days "$LOOKBACK_DAYS"
fi

.venv/bin/python -m app.cli collect \
  --output docs/data/publications.json \
  --days "$LOOKBACK_DAYS"
.venv/bin/python -m app.cli validate \
  --input docs/data/publications.json \
  --require-v8 \
  --max-age-hours 1.5
.venv/bin/python -m app.cli validate-calendars --input docs/data/calendars.json
if [[ -f package-lock.json ]]; then
  npm ci
  npm test
  npm run test:e2e
fi

echo "Cut generated and validated locally. No commit or push was performed."
