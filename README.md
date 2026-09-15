# careers-os

A personal AI-assisted job search / career operating system. It discovers
real jobs from multiple sources (Creative Circle, Greenhouse), normalizes
them into a source-agnostic canonical schema, persists them, and scores
them for career fit — including a real, evidence-based `experience_fit`
matched against your own imported resume history, not just keywords.

See `docs/architecture.md` for the full pipeline and design principle
("sources change, the career model should not"), `docs/scoring.md` for how
fit scoring works, `docs/evidence-model.md` for how job requirements are
matched against career evidence, `docs/resume-model.md` for the resume
import/data model, and `docs/sources/*.md` for what was actually
discovered about each source's backend.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Optional: copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` to
enable the optional AI semantic evaluation layer (scoring works fully
without it — deterministic scoring, including `experience_fit`, never
requires an API key).

`jobs`/`careers` are zsh/bash builtin-adjacent names — if your shell
swallows the command, prefix it with `command`.

## Import your resume(s) first

```bash
command careers import-resume ~/path/to/resume.docx --variant fde
command careers resumes
command careers experience
```

Deterministic, no LLM required — parses the document's own paragraph
styles (see `docs/resume-model.md`). Importing more than one variant of
the same underlying career history is expected and supported; shared
employers are reconciled automatically, not duplicated.

## Search and score real jobs

```bash
command jobs search creative-circle --query "solutions architect" --remote --days 30
command jobs search greenhouse --board anthropic --query "solutions architect"
command jobs search greenhouse-all --query "forward deployed engineer"
```

Once a resume is imported, every search automatically scores
`experience_fit` against your real evidence — no extra flag needed.

## Dig into one job

```bash
command jobs show <source> <source_job_id>          # full detail incl. matches/gaps/recommendation
command jobs match <source> <source_job_id>          # every requirement match, grouped
command jobs evidence <source> <source_job_id>       # best cited evidence for strong matches
command jobs gaps <source> <source_job_id>           # classified gaps (real / resume-language / interview-prep)
command jobs recommend-resume <source> <source_job_id>
command jobs status <source> <source_job_id> saved   # survives future re-syncs
```

## Other commands

```bash
command jobs list --status saved
command jobs health greenhouse --board anthropic
command jobs duplicates   # cross-source possible-duplicate flags (never auto-merged)
command jobs history <source> <source_job_id>   # field-level change log across re-syncs
```

Data persists to `data/careers.db` (SQLite, gitignored).

## Tests

```bash
pytest              # fixture-based, fully offline
pytest -m live      # optional: hits the real Creative Circle and Greenhouse APIs
```
