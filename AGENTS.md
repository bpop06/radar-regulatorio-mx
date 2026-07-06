# Repository Guidance

## Purpose

Build and maintain a daily Mexican regulatory intelligence service focused on
tax, customs, foreign trade, intellectual property, federal administrative
law, administrative litigation, and tax-specific administrative litigation.

## Layout

- `docs/` is the public site served by GitHub Pages ("Deploy from a branch",
  `main` + `/docs`). The daily data file is `docs/data/publications.json`.
- The daily automation skill lives in `.agents/skills/radar-diario/SKILL.md`
  and is executed on the Mac via `scripts/codex_daily.sh` (see
  `docs/MAC_SCHEDULE.md`).

## Engineering rules

- Keep each public source in an isolated collector.
- Prefer official APIs or feeds; use HTML parsing only when no structured
  interface exists.
- Preserve the official source URL and publication date for every record.
- Deduplicate by stable source identifier or canonical URL.
- Do not publish a generated summary unless it contains exactly 30 words.
- Treat tax administrative litigation as a subset of administrative
  litigation. Tax collection or enforcement alone is not contentious
  procedure.
- Cover LFPA, LOAPF, organic rules, delegated powers, federal organization,
  and appointments across the Federal Public Administration.
- A collector failure must not prevent successful sources from being published.
- Add parser fixtures and tests when changing source-specific extraction.
- Never commit API keys, tokens, local databases, or downloaded bulk documents.

## Verification

Run before committing (from the repo's `.venv`; bare `python` may not exist
on macOS):

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m app.cli collect --dry-run
```

## Git

- `main` must remain deployable.
- Use short-lived branches prefixed with `codex/`.
- Write imperative Conventional Commit messages.
- Keep generated data changes separate from source-code changes when practical.
