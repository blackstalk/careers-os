# careers-os

A personal AI-assisted job search / career operating system. It discovers
real jobs from multiple sources (Creative Circle, Greenhouse), normalizes
them into a source-agnostic canonical schema, matches them against your
own imported resume evidence, and — beyond a simple fit score — decides
whether a job is actually worth pursuing: is it eligible for you, are you
credibly qualified, does it move your career forward, and does the value
justify the time, with hard gates (eligibility, a missing must-have
qualification) able to override an otherwise-strong match.

See `docs/architecture.md` for the full pipeline and design principle
("sources change, the career model should not"), `docs/scoring.md` for
fit scoring, `docs/evidence-model.md` for evidence matching,
`docs/discovery.md` for multi-profile discovery and ranking,
`docs/eligibility.md` and `docs/pursue-recommendation.md` for the
opportunity-decision layer (eligibility, qualification gates, optional
AI evidence reasoning with enforced guardrails, opportunity cost, and the
final pursue recommendation), `docs/resume-model.md` for the resume
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

## Discover broadly and see what's actually worth pursuing

```bash
command jobs discover                                   # every enabled profile, every source, ranked
command jobs discover --profile laravel --profile craft --remote --limit 10
command jobs discover --source creative-circle --min-fit 0.5
```

Configurable search profiles live in `career/data/search_profiles.yaml` —
add a profile without touching code. Each result leads with a `Pursue`
recommendation (`strong_pursue` / `pursue` / `consider` / `low_priority` /
`verify_first` / `do_not_pursue`), not just a raw fit percentage — see
`docs/discovery.md` and `docs/pursue-recommendation.md`.

## Dig into one job

```bash
command jobs evaluate <source> <source_job_id>       # full decision chain: eligibility, qualification,
                                                      # transferable evidence, opportunity cost, pursue rec
command jobs show <source> <source_job_id>           # full detail incl. matches/gaps/recommendation/pursue
command jobs match <source> <source_job_id>          # every requirement match, grouped
command jobs evidence <source> <source_job_id>       # best cited evidence for strong matches
command jobs gaps <source> <source_job_id>           # classified gaps (real / resume-language / interview-prep)
command jobs recommend-resume <source> <source_job_id>
command jobs status <source> <source_job_id> saved   # survives future re-syncs
```

Candidate facts used for eligibility checks (location, timezone, work
authorization, relocation willingness, security clearance) live in
`career/data/candidate.yaml` — edit it to match your own situation.

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
