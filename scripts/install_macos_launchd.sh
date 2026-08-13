#!/usr/bin/env bash
set -euo pipefail

echo "error: the direct macOS collection LaunchAgent is retired." >&2
echo "Use the single Codex project automation documented in docs/OPERATIONS.md." >&2
exit 1
