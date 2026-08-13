#!/usr/bin/env bash
set -euo pipefail

# Compatibility entry point. Scheduling belongs to the single Codex project
# automation. This helper only executes the safe, non-publishing cut command.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$ROOT/scripts/collect_daily.sh" "$@"
