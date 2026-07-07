# Repository Guidance

## Purpose

Build and maintain a daily Mexican regulatory intelligence service focused on
tax, customs, foreign trade, intellectual property, federal administrative
law, administrative litigation, and tax-specific administrative litigation.

## Layout

- `docs/` is the public site served by GitHub Pages ("Deploy from a branch",
  `main` + `/docs`). The daily data file is `docs/data/publications.json`.
- Daily operation is split: the Mac collects and publishes extractive data
  (`scripts/codex_daily.sh` → `collect_daily.sh`, see `docs/MAC_SCHEDULE.md`);
  a daily Claude routine in the cloud writes the editorial layer through
  `python -m app.cli apply-editorial` and audits the cut using the read-only
  guide `.agents/skills/radar-diario/SKILL.md` (see `docs/EDITORIAL_CLOUD.md`).

## Engineering rules

- Keep each public source in an isolated collector.
- Prefer official APIs or feeds; use HTML parsing only when no structured
  interface exists.
- Preserve the official source URL and publication date for every record.
- Deduplicate by stable source identifier or canonical URL.
- Do not publish a generated summary unless it contains exactly 30 words.
- Editorial titles lead with the substance ("organ + verb + what changed");
  office/act numbers are secondary metadata and must not open a title.
- Every published item carries `card_body` with the fixed sections
  "## Qué se publicó", "## Sustancia" and "## Fuente".
- Taxonomy is deterministic (`app/taxonomy.py`): issuing body resolved from
  the organ catalog, jurisdiction/country from the source origin map, date
  facets from `published_at`, importance bucketed 1-5 from relevance.
- The local SQLite history (`data/radar.sqlite3`) is never committed; the
  public JSON remains the only published data contract.
- Treat tax administrative litigation as a subset of administrative
  litigation. Tax collection or enforcement alone is not contentious
  procedure.
- Cover LFPA, LOAPF, organic rules, delegated powers, federal organization,
  and appointments across the Federal Public Administration.
- A collector failure must not prevent successful sources from being published.
- Add parser fixtures and tests when changing source-specific extraction.
- Never commit API keys, tokens, local databases, or downloaded bulk documents.

## Dependencies

Runtime and dev dependencies are pinned with hashes in `requirements.txt` and
`requirements-dev.txt`, generated from `pyproject.toml` with `uv`. Install with
`pip install --require-hashes -r requirements.txt` (or `requirements-dev.txt`),
then `pip install -e . --no-deps` for the local package. To change a version,
edit `pyproject.toml` and regenerate both files:

```bash
uv pip compile pyproject.toml --generate-hashes -o requirements.txt
uv pip compile --extra dev pyproject.toml --generate-hashes -o requirements-dev.txt
```

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
