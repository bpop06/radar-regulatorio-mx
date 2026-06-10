# Repository Guidance

## Purpose

Build and maintain a daily Mexican regulatory intelligence service focused on
tax, customs, foreign trade, intellectual property, and tax procedure.

## Engineering rules

- Keep each public source in an isolated collector.
- Prefer official APIs or feeds; use HTML parsing only when no structured
  interface exists.
- Preserve the official source URL and publication date for every record.
- Deduplicate by stable source identifier or canonical URL.
- Do not publish a generated summary unless it contains exactly 30 words.
- A collector failure must not prevent successful sources from being published.
- Add parser fixtures and tests when changing source-specific extraction.
- Never commit API keys, tokens, local databases, or downloaded bulk documents.

## Verification

Run before committing:

```bash
python -m pytest
python -m ruff check .
python -m app.cli collect --dry-run
```

## Git

- `main` must remain deployable.
- Use short-lived branches prefixed with `codex/`.
- Write imperative Conventional Commit messages.
- Keep generated data changes separate from source-code changes when practical.

