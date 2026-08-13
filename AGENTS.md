# Repository Guidance

## Purpose

Build and operate a daily Mexican regulatory intelligence service focused on
tax, customs, foreign trade, intellectual property, federal administrative
law, administrative litigation, and tax-specific administrative litigation.

## Public contract

- Public data uses `schema_version: 8`.
- Identity is `source + source_id`; canonical URL is the fallback. Never
  deduplicate by title and date.
- Preserve `canonical_url`, `official_published_at`, `detected_at`,
  `content_hash`, `first_seen_at` and `last_seen_at`.
- Private editorial state is `pending`, `needs_review` or `complete`; public
  artifacts contain only `complete` records.
- A complete item needs an editorial title, a teaser of at most 70 words, a
  300-1000 word summary, structured card content, official identifiers and
  official evidence.
- A needs-review item stays in the private SQLite queue with its official
  metadata and review reason. Do not fabricate or expose a provisional title.
- International cases keep number, parties, claim, treaty, status, outcome,
  reasoning and amount in separate fields.

## Publication

- `docs/` is served by GitHub Pages from `main/docs`.
- Generate the complete cut in staging, validate it and swap it as one unit.
  A failed run or `--dry-run` must not mutate public artifacts or source state.
- A cut includes `edition.json`, `manifest.json`, compatibility
  `publications.json`, monthly archive indexes, immutable item JSON, static
  notes and transactional source state.
- Preserve completed editorial work when `content_hash` is unchanged. New or
  changed evidence returns to `needs_review`.
- A single source failure yields honest degraded coverage. A total failure
  preserves the previous cut.
- Keep each official source in an isolated collector. Prefer official APIs or
  feeds; use HTML only when no structured interface exists.

## Editorial policy

- Codex performs interpretation from official evidence only. Python never calls
  a model API.
- Do not infer a treaty, legal effect, amount, status or outcome absent from
  official evidence.
- Apply edits only through `python -m app.cli apply-editorial`; the operation
  is validated and all-or-nothing.

## Verification

Run before committing:

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
.venv/bin/python -m app.cli collect --dry-run
.venv/bin/python -m app.cli validate --input docs/data/publications.json
.venv/bin/python -m app.cli validate-calendars --input docs/data/calendars.json
npm test
npm run test:e2e
```

Add fixtures for every source parser change and regression tests for contract,
transaction, editorial preservation, archive permanence and accessibility.

## Git and operation

- `main` must remain deployable.
- Use short-lived `codex/` branches and imperative Conventional Commits.
- Production data reaches `main` only through pull requests. Never push a cut
  directly to `main`.
- The only recurring job is the Codex project automation documented in
  `docs/OPERATIONS.md`; it uses a clean worktree and one
  `codex/radar-daily` PR.
- Never commit API keys, tokens, local databases or downloaded bulk documents.
