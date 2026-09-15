# Source: Ashby (public Job Postings API)

Investigated 2026-09-15 against several real companies' public job
boards (OpenAI, Ramp, Perplexity, WorkOS) as part of Phase 4's discovery
expansion (docs/alerts.md, docs/architecture.md) — the goal was better
coverage of Forward Deployed Engineer, Solutions Architect, Applied AI,
and ML Systems roles than Creative Circle or the currently-configured
Greenhouse boards provide.

## How discovery works

A genuinely documented public API:
https://developers.ashbyhq.com/docs/public-job-posting-api. Explicitly
built by Ashby for external aggregation — the docs name LinkedIn,
Indeed, Otta, Built In, and ZipRecruiter as intended consumers, which is
also why this was chosen as the primary new source over an ad hoc
LinkedIn/Indeed integration (neither offers a compliant, unauthenticated
programmatic path — see docs/source-evaluation.md).

- `GET https://api.ashbyhq.com/posting-api/job-board/{board_name}?includeCompensation=true`
  — every published job on a board, in one response.

Unauthenticated. There is **no separate per-job detail endpoint** — the
list response already includes full HTML/plain-text descriptions and
structured compensation, so `AshbySource.fetch_job` re-fetches the
board's full list and finds the matching id (see "Adapter shape" below).

### Multi-tenant, per-company boards

A `board_name` (e.g. `openai`, `ramp`) identifies which company's
postings you get, mirroring Greenhouse's `board_token` pattern exactly.
Confirmed live: `openai` (808 postings, 73 matching Forward
Deployed/Solutions/Applied AI title keywords at investigation time),
`ramp`, `perplexity`, `workos`. Not every candidate company uses Ashby —
`perplexityai` (the more obvious slug guess) 404s; the real slug is
`perplexity`.

## Adapter shape (why one instance = one board)

`AshbySource` is constructed with a `board_name` and only ever talks to
that one board — see `sources/ashby/source.py`. Searching several
companies means instantiating several `AshbySource`s (see `jobs search
ashby-all`, which loops over `sources/ashby/boards.yaml`), identical to
`GreenhouseSource`. `NormalizedJob.source` is always the constant
`"ashby"` (never `"ashby:<board>"`) for the same cross-source
`(source, source_job_id)` dedup reason documented in
docs/architecture.md — the board name lives inside `source_job_id`
(`"<board>:<id>"`).

Because there's no per-job detail endpoint, `fetch_job` re-lists the
whole board and searches for the matching id client-side, rather than
hitting a dedicated URL. This is more expensive per call than
Greenhouse's direct detail fetch, but `fetch_job` currently has no
caller anywhere in the pipeline outside `sources/` itself (verified by
grep before implementing) — it exists to satisfy the `JobSource`
contract, not because it's on a hot path today.

## What's different from Greenhouse (third-source validation)

- **Structured compensation, not free text.** This is Ashby's biggest
  advantage over both existing sources. `compensation.compensationTiers[]
  .components[]` gives real numeric `minValue`/`maxValue` per component,
  tagged `compensationType: "Salary"` — no regex-scraping of description
  HTML required (contrast with `docs/sources/greenhouse.md`'s
  `SALARY_TEXT_PATTERN` heuristic). `parser.extract_salary_range` takes
  the min-of-mins and max-of-maxes across every `Salary` component in
  every tier, since a posting frequently has multiple tiers (e.g. one per
  office location) and there's no principled way to pick "the" tier —
  this reports the full range actually offered, not a guess at which
  location applies.
- **Structured `employmentType` and (partially) `workplaceType`.**
  `employmentType` is a clean enum-like string (`FullTime`/`Contract`/
  `PartTime`/`Intern`/`Temporary`) — no heuristic text scanning needed,
  unlike Greenhouse. `workplaceType` (`OnSite`/`Remote`/`Hybrid`) is
  structured too, but **frequently null even on real, currently-listed
  jobs** (observed on OpenAI's own "Applied AI Architect" posting) — the
  boolean `isRemote` field is checked as a fallback, not because it's
  more reliable, but because it's occasionally populated when
  `workplaceType` isn't. Both can be absent; `RemoteStatus.UNKNOWN` is
  the honest result in that case, same philosophy as every other source.
- **No per-job detail endpoint at all** (see "Adapter shape" above) —
  the opposite of Greenhouse, which has one but no structured
  compensation.
- **No company-name field per job.** Unlike Greenhouse's
  `company_name` (when present), Ashby's job payload never includes the
  employer's display name — `NormalizedJob.company` falls back to the
  board name itself, same fallback Greenhouse already uses when
  `company_name` is absent.
- **Location is already a plain string**, not a nested object needing
  extraction the way Greenhouse's `location.name` is — simpler parsing.

No change was needed to `domain/job.py`, `domain/query.py`,
`sources/base.py`, `storage/`, or `scoring/` to support Ashby — every
adaptation above lives entirely inside `sources/ashby/`.

## Rate limiting and retrieval hygiene

Same posture as the other adapters (`sources/ashby/client.py`): honest
User-Agent, 15s timeout, exponential-backoff retry, 1-second minimum
delay between requests. No documented or observed rate limit —
precautionary, not a response to an observed throttle.

## Test strategy

- `tests/fixtures/ashby/jobs_list_response.json` — hand-built fixture
  covering: a posting with a structured salary tier, a remote posting via
  `workplaceType`/`isRemote`, a contract posting, a posting with neither
  remote nor compensation signal, and a malformed item missing `id`.
- `tests/test_ashby_parser.py` — parsing/normalization, fully offline,
  including multi-tier compensation aggregation.
- `tests/test_source_ashby.py` — client-side filtering, pagination,
  sorting, board-not-found handling, `fetch_job`'s list-and-find
  behavior, health reporting — via `httpx.MockTransport`, fully offline.
- `tests/test_live_smoke_ashby.py` — hits the real API against the
  `openai` board, marked `@pytest.mark.live`, excluded by default. Run
  explicitly with `pytest -m live`.
