#!/usr/bin/env bash
set -euo pipefail

# Execute the local Codex release gates and mirror their result to GitHub's
# legacy commit-status API. This script intentionally makes no commits or
# pushes; it only reports verification for an existing commit.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_CONTEXT="Codex local / Python"
FRONTEND_CONTEXT="Codex local / Frontend"

validate_repository() {
  local repository="$1"

  if [[ ! "$repository" =~ ^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    echo "error: RADAR_GITHUB_REPOSITORY must be a GitHub owner/name pair" >&2
    return 1
  fi
}

validate_sha() {
  local sha="$1"

  if [[ ! "$sha" =~ ^[0-9A-Fa-f]{40}$ ]]; then
    echo "error: RADAR_GIT_SHA must be a 40-character hexadecimal commit SHA" >&2
    return 1
  fi
}

validate_pr_number() {
  local pr_number="$1"

  if [[ ! "$pr_number" =~ ^[1-9][0-9]*$ ]]; then
    echo "error: RADAR_PR_NUMBER must be a positive integer" >&2
    return 1
  fi
}

ensure_clean_worktree() {
  local worktree_status

  if [[ "${RADAR_ALLOW_DIRTY:-0}" == "1" ]]; then
    return 0
  fi

  if ! worktree_status="$(git status --porcelain)"; then
    echo "error: could not inspect the Git worktree" >&2
    return 1
  fi

  if [[ -n "$worktree_status" ]]; then
    echo "error: the local Codex CI requires a clean worktree" >&2
    echo "set RADAR_ALLOW_DIRTY=1 only for an intentional override" >&2
    return 1
  fi
}

ensure_exact_head() {
  local expected_sha="$1"
  local actual_sha

  if ! actual_sha="$(git rev-parse HEAD)"; then
    echo "error: could not resolve the local Git HEAD" >&2
    return 1
  fi
  expected_sha="$(printf '%s' "$expected_sha" | tr '[:upper:]' '[:lower:]')"
  if [[ "$actual_sha" != "$expected_sha" ]]; then
    echo "error: RADAR_GIT_SHA does not match the checked-out HEAD" >&2
    return 1
  fi
}

ensure_open_pr_head() {
  local repository="$1"
  local pr_number="$2"
  local expected_sha="$3"
  local pr_data
  local pr_sha
  local pr_repository
  local base_ref
  local state

  if ! pr_data="$(gh api "repos/${repository}/pulls/${pr_number}" \
    --jq '[.head.sha, .head.repo.full_name, .base.ref, .state] | @tsv')"; then
    echo "error: could not read pull request ${pr_number}" >&2
    return 1
  fi
  IFS=$'\t' read -r pr_sha pr_repository base_ref state <<<"$pr_data"
  if [[ "$pr_sha" != "$expected_sha" ]]; then
    echo "error: the pull request head does not match RADAR_GIT_SHA" >&2
    return 1
  fi
  if [[ "$pr_repository" != "$repository" ]]; then
    echo "error: CI only accepts a same-repository pull request" >&2
    return 1
  fi
  if [[ "$base_ref" != "main" || "$state" != "open" ]]; then
    echo "error: CI requires an open pull request targeting main" >&2
    return 1
  fi
}

post_status() {
  local repository="$1"
  local sha="$2"
  local target_url="$3"
  local state="$4"
  local context="$5"
  local description="$6"

  if [[ "${RADAR_SKIP_STATUS_POST:-0}" == "1" ]]; then
    echo "Skipping GitHub status post: ${context} (${state})"
    return 0
  fi

  gh api --method POST "repos/${repository}/statuses/${sha}" \
    -f "state=${state}" \
    -f "context=${context}" \
    -f "target_url=${target_url}" \
    -f "description=${description}" >/dev/null
}

run_python_gate() {
  .venv/bin/python -m ruff check . || return 1
  .venv/bin/python -m pytest || return 1
  .venv/bin/python -m app.cli collect --dry-run || return 1
  .venv/bin/python -m app.cli validate --input docs/data/publications.json --require-v8 \
    --max-age-hours 1.5 || return 1
  .venv/bin/python -m app.cli validate-calendars || return 1
  git diff --check || return 1
}

run_frontend_gate() {
  npm ci || return 1
  npm test || return 1
  npx playwright install chromium || return 1
  npm run test:e2e || return 1
}

main() {
  cd "$ROOT"

  local repository="${RADAR_GITHUB_REPOSITORY:-bpop06/radar-regulatorio-mx}"
  local sha="${RADAR_GIT_SHA:-$(git rev-parse HEAD)}"
  local pr_number="${RADAR_PR_NUMBER:-}"
  local target_url

  validate_repository "$repository"
  validate_sha "$sha"
  validate_pr_number "$pr_number"
  target_url="${RADAR_STATUS_TARGET_URL:-https://github.com/${repository}/pull/${pr_number}}"

  ensure_clean_worktree
  ensure_exact_head "$sha"
  ensure_open_pr_head "$repository" "$pr_number" "$sha"

  post_status "$repository" "$sha" "$target_url" pending "$PYTHON_CONTEXT" \
    "Python checks are running"
  post_status "$repository" "$sha" "$target_url" pending "$FRONTEND_CONTEXT" \
    "Frontend checks are waiting for Python"
  if run_python_gate; then
    post_status "$repository" "$sha" "$target_url" success "$PYTHON_CONTEXT" \
      "Python checks passed"
  else
    post_status "$repository" "$sha" "$target_url" failure "$PYTHON_CONTEXT" \
      "Python checks failed"
    return 1
  fi

  if run_frontend_gate; then
    post_status "$repository" "$sha" "$target_url" success "$FRONTEND_CONTEXT" \
      "Frontend checks passed"
  else
    post_status "$repository" "$sha" "$target_url" failure "$FRONTEND_CONTEXT" \
      "Frontend checks failed"
    return 1
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
