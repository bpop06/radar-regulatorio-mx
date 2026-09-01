from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_codex_ci.sh"


def _run_shell(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f"source {shlex.quote(str(SCRIPT))}\n{body}"],
        capture_output=True,
        check=False,
        text=True,
    )


def test_run_codex_ci_has_valid_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_run_codex_ci_declares_required_contexts_and_commands() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'PYTHON_CONTEXT="Codex local / Python"' in script
    assert 'FRONTEND_CONTEXT="Codex local / Frontend"' in script
    assert 'gh api --method POST "repos/${repository}/statuses/${sha}"' in script
    assert 'gh api "repos/${repository}/pulls/${pr_number}"' in script

    expected_commands = (
        ".venv/bin/python -m ruff check .",
        ".venv/bin/python -m pytest",
        ".venv/bin/python -m app.cli collect --dry-run",
        "jq -e '.publication_policy == \"complete-only\"' docs/data/manifest.json",
        ".venv/bin/python -m app.cli validate --input docs/data/publications.json --require-v8",
        "--max-age-hours 1.5",
        ".venv/bin/python -m app.cli validate-calendars",
        "git diff --check",
        "npm ci",
        "npm test",
        "npx playwright install chromium",
        "npm run test:e2e",
    )

    for command in expected_commands:
        assert command in script


@pytest.mark.parametrize(
    ("repository", "expected_returncode"),
    (
        ("bpop06/radar-regulatorio-mx", 0),
        ("owner/repository/extra", 1),
        ("owner name/repository", 1),
        ("owner/", 1),
    ),
)
def test_repository_validation_is_fail_closed(
    repository: str, expected_returncode: int
) -> None:
    result = _run_shell(
        f"if validate_repository {shlex.quote(repository)}; then exit 0; else exit 1; fi"
    )

    assert result.returncode == expected_returncode


@pytest.mark.parametrize(
    ("sha", "expected_returncode"),
    (
        ("a" * 40, 0),
        ("a" * 39, 1),
        ("g" * 40, 1),
    ),
)
def test_sha_validation_requires_a_full_hex_commit_sha(sha: str, expected_returncode: int) -> None:
    result = _run_shell(f"if validate_sha {shlex.quote(sha)}; then exit 0; else exit 1; fi")

    assert result.returncode == expected_returncode


def test_status_posting_can_be_skipped_without_calling_gh() -> None:
    sha = "a" * 40
    target_url = f"https://github.com/bpop06/radar-regulatorio-mx/commit/{sha}"

    result = _run_shell(
        f"""
gh() {{
  return 99
}}
RADAR_SKIP_STATUS_POST=1
post_status "bpop06/radar-regulatorio-mx" "{sha}" \\
  "{target_url}" \\
  pending "$PYTHON_CONTEXT" "Python checks are running"
"""
    )

    assert result.returncode == 0, result.stderr
    assert "Skipping GitHub status post" in result.stdout


def test_exact_head_rejects_another_well_formed_sha() -> None:
    result = _run_shell(f"ensure_exact_head {'0' * 40}")

    assert result.returncode == 1
    assert "does not match" in result.stderr


@pytest.mark.parametrize(
    ("value", "valid"),
    (("16", True), ("1", True), ("", False), ("0", False), ("1x", False)),
)
def test_pr_number_validation(value: str, valid: bool) -> None:
    result = _run_shell(
        f"if validate_pr_number {shlex.quote(value)}; then exit 0; else exit 1; fi"
    )

    assert (result.returncode == 0) is valid


def test_pull_request_head_must_match_same_repository_and_main() -> None:
    sha = "a" * 40
    result = _run_shell(
        f"""
gh() {{
  printf '%s\\t%s\\t%s\\t%s\\n' '{sha}' 'bpop06/radar-regulatorio-mx' 'main' 'open'
}}
ensure_open_pr_head 'bpop06/radar-regulatorio-mx' 16 '{sha}'
"""
    )
    assert result.returncode == 0, result.stderr

    mismatch = _run_shell(
        f"""
gh() {{
  printf '%s\\t%s\\t%s\\t%s\\n' '{'b' * 40}' 'bpop06/radar-regulatorio-mx' 'main' 'open'
}}
ensure_open_pr_head 'bpop06/radar-regulatorio-mx' 16 '{sha}'
"""
    )
    assert mismatch.returncode == 1
    assert "head does not match" in mismatch.stderr


def test_both_contexts_are_reset_to_pending_before_gates() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    python_pending = script.index(
        'pending "$PYTHON_CONTEXT"', script.index("ensure_exact_head")
    )
    frontend_pending = script.index(
        'pending "$FRONTEND_CONTEXT"', script.index("ensure_exact_head")
    )
    python_gate = script.index("if run_python_gate")

    assert python_pending < python_gate
    assert frontend_pending < python_gate


def test_python_failure_marks_frontend_context_as_terminal() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    python_failure = script.index('failure "$PYTHON_CONTEXT"')
    frontend_failure = script.index('failure "$FRONTEND_CONTEXT"', python_failure)
    return_after_failure = script.index("return 1", python_failure)

    assert python_failure < frontend_failure < return_after_failure
    assert "Frontend checks not run: Python checks failed" in script
