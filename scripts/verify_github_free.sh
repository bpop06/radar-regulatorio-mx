#!/usr/bin/env bash
set -euo pipefail

# Guard the repository's zero-cost contract. GitHub Actions is retained only
# as an optional manual diagnostic; scheduled Codex tasks provide the required
# local CI statuses without consuming Actions or depending on GitHub billing.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

status=0
workflow_count=0

while IFS= read -r workflow; do
  workflow_count=$((workflow_count + 1))

  while IFS= read -r declaration; do
    label="${declaration#*:}"
    label="${label%%#*}"
    label="${label//[[:space:]\"\']/}"
    if [[ "$label" != "ubuntu-latest" ]]; then
      echo "error: $workflow uses non-standard runner '$label'" >&2
      status=1
    fi
  done < <(grep -E '^[[:space:]]*runs-on:' "$workflow" || true)

  if grep -Eq '^[[:space:]]*merge_group:[[:space:]]*($|#)' "$workflow"; then
    echo "error: $workflow enables a merge queue via merge_group" >&2
    status=1
  fi

  if grep -Eq '^[[:space:]]*(pull_request|push|schedule):[[:space:]]*($|#)' \
    "$workflow"; then
    echo "error: $workflow must remain manual-only; Codex provides CI" >&2
    status=1
  fi
done < <(find .github/workflows -type f \( -name '*.yml' -o -name '*.yaml' \) -print | sort)

if [[ "$workflow_count" -eq 0 ]]; then
  echo "error: no GitHub Actions workflows found" >&2
  exit 1
fi

if ! grep -Fq 'test -f docs/data/manifest.json' .github/workflows/ci.yml; then
  echo "error: CI must require docs/data/manifest.json" >&2
  status=1
fi

if ! grep -Fq 'python -m app.cli validate --input docs/data/publications.json --require-v8' \
  .github/workflows/ci.yml; then
  echo "error: CI must validate the public cut with --require-v8" >&2
  status=1
fi

if grep -Eq 'if[[:space:]]+\[[[:space:]]+-f[[:space:]]+docs/data/manifest\.json' \
  .github/workflows/ci.yml; then
  echo "error: CI must not fall back when the v8 manifest is absent" >&2
  status=1
fi

for context in 'Codex local / Python' 'Codex local / Frontend'; do
  if ! grep -Fq "$context" scripts/run_codex_ci.sh; then
    echo "error: local Codex CI must publish '$context'" >&2
    status=1
  fi
done

if ! grep -Fq 'ensure_exact_head "$sha"' scripts/run_codex_ci.sh; then
  echo "error: local Codex CI must bind statuses to the checked-out HEAD" >&2
  status=1
fi

if [[ "$status" -ne 0 ]]; then
  exit "$status"
fi

echo "Zero-cost GitHub/Codex contract verified ($workflow_count manual workflows)."
