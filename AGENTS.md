# AGENTS.md

# pdf-bill-info-extractor — Codex Project Instructions

These instructions apply to all Codex work in this repository unless the user
explicitly overrides a specific rule for a specific task.

## 1. Privacy Is the Highest Priority

This repository may contain private financial statements and generated
financial data.

By default, DO NOT open, read, parse, inspect, summarize, search, grep, cat,
print, enumerate filenames from, or otherwise access the contents of:

- `source_input/`
- `editable_input/`
- `redacted_input/`
- `tests/input/`
- `tests/output/`
- real financial contents under `csv_output/`
- any real `*.pdf` statement file

Do not traverse these locations with:

- `find`
- `rg`
- `grep`
- `ls`
- `Get-ChildItem`
- Python directory traversal
- similar filesystem-search commands

unless the user explicitly authorizes that access for the current task.

If a task appears to require private financial content, STOP and explain what
privacy-safe structural information is required instead of inspecting the
private data.

## 2. Never Expose Private Financial Content

Do not ask the user to paste or provide:

- account numbers
- balances
- transaction descriptions
- merchant names
- financial amounts
- customer names
- addresses
- passwords
- private redaction terms
- other personal financial identifiers

Prefer privacy-safe structural information such as:

- page counts
- table headers
- column order
- row counts
- parser/profile IDs
- date formats
- bounding boxes or X positions
- structural labels
- success/failure counts
- hashes

Real/private redaction terms and PDF passwords must never appear in:

- logs
- exceptions
- result dataclasses
- generated summaries
- workflow CSVs
- test fixtures
- Git history

Real/private redaction terms may be persisted only in the Git-ignored local
file `config/redaction_terms.local.json`. They must never be committed,
logged, copied into tests, or written to any other tracked file.

PDF passwords must never be persisted.

Synthetic sentinel/example values may be used in synthetic tests when needed
to verify privacy behavior.

## 3. Synthetic Tests By Default

All Codex-created tests must use synthetic data unless explicitly authorized
otherwise.

Prefer:

- `TemporaryDirectory`
- generated PDFs
- generated CSVs
- synthetic profile objects
- synthetic transaction rows

Do not use real statements or real financial CSV contents as test fixtures.

Codex may run focused synthetic tests after inspecting the test source and
confirming they do not access private data.

Do not run the full private statement corpus unless the user explicitly
authorizes it.

Normally the user performs private-corpus validation manually.

## 4. Git Safety

Before making changes:

1. Confirm the repository root.
2. Confirm the current branch.
3. Check `git status --short`.
4. If the working tree contains unexpected changes, STOP and report them.

Do not discard, overwrite, reset, clean, or revert user changes unless the
user explicitly requests it.

Do not commit or push unless the user explicitly authorizes the commit/push
for the current task.

Do not create unrelated commits.

Before reporting implementation complete, normally run:

```bash
git diff --check
git status --short
git diff --stat
```
