# Source: Greenhouse (public Job Board API)

Investigated 2026-09-14 against several real companies' public boards
(Anthropic, Stripe, Figma, Brex, Robinhood).

## How discovery works

Unlike Creative Circle, this is a **genuinely documented public API**:
https://developers.greenhouse.io/job-board.html. No reverse engineering
was required for the endpoint shapes — what still had to be discovered by
direct testing is documented below, because the official docs don't cover
operational behavior (pagination, per-board field consistency, content
encoding quirks).

- `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs` — every job on a board
- `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{id}` — one job's detail

Both are unauthenticated. `?content=true` on the list endpoint additionally
returns `content` (the HTML description), `departments`, and `offices` —
without it, only the bare fields (title, location, dates, metadata) come
back. This adapter always requests `content=true`.

### Multi-tenant, per-company boards

A `board_token` (e.g. `anthropic`, `stripe`) identifies which company's
postings you get — confirmed by testing several real companies' tokens
directly. Not every company uses Greenhouse (`doordash` returned 404 in
testing), and a token isn't guaranteed to match a company's public
careers-page slug exactly.

## Adapter shape (why one instance = one board)

`GreenhouseSource` is constructed with a `board_token` and only ever talks
to that one board — see `sources/greenhouse/source.py`. Searching several
companies means instantiating several `GreenhouseSource`s (see `jobs
search greenhouse-all`, which loops over `sources/greenhouse/boards.yaml`),
rather than one adapter fanning out internally. `NormalizedJob.source` is
always the constant `"greenhouse"` (never `"greenhouse:<board>"`) so that
`(source, source_job_id)` deduplication (docs/architecture.md) stays
consistent across sources — the board token instead lives inside
`source_job_id` (`"<board>:<id>"`), which is also what lets `fetch_job`
know which per-board endpoint to hit from a bare id string.

## What was different from Creative Circle (second-source validation)

Building this adapter against a genuinely different platform surfaced real
places where Phase 1's design either held up cleanly or needed adjusting:

- **No server-side search, filter, or pagination at all.** The Job Board
  API returns literally every job for a board in one response (596 for
  Anthropic at investigation time) with no `keyword`/`page`/`rows`
  equivalent. `JobSearchQuery` itself needed no changes — the `JobSource`
  contract never promised server-side filtering — but `GreenhouseSource`
  has to implement every filter (keyword, location, remote, employment
  type, posted-within-days, sort, pagination) **entirely client-side**
  over the full list (see `source.py`). This is the clearest proof the
  `search(query) -> list[RawJob]` abstraction was drawn in the right
  place: nothing outside the adapter needed to know or care which side did
  the filtering.
- **No standardized remote-status or employment-type field.** Creative
  Circle has a clean `workLocationTypeId` enum; Greenhouse has none. Each
  company defines its own free-form `metadata` fields — testing multiple
  boards found completely different label sets (Anthropic: "Location
  Type"; Brex: "Job Post Department"; Robinhood: "Careers Page Bucket";
  Stripe and Figma: no custom metadata at all). `parser.detect_remote_status`
  and `detect_employment_type` are therefore heuristic and low-confidence
  by design, checking a small set of known label spellings and falling
  back to scanning `location.name` text — verified against real data: one
  Anthropic role's location string says "Remote-Friendly" while its actual
  structured metadata says "On-Site", and the metadata correctly wins.
- **No compensation field.** Salary isn't structured data here. Some
  companies (observed: Anthropic, on ~79% of postings) embed a figure as
  free-text boilerplate inside the description (e.g. "Annual Salary:
  $222,800 — $290,000 USD"); `parser.extract_salary_from_text` does a
  best-effort regex extraction of that specific pattern. This is
  fundamentally less reliable than Creative Circle's `SalaryMin`/`SalaryMax`
  fields and is scored accordingly — nothing here should be trusted the
  way a real structured field is.
- **Double HTML-escaped content.** The `content` field's HTML has been
  HTML-entity-escaped an *extra* time (`&lt;div&gt;` literally, not `<div>`)
  — `parser.html_to_text` unescapes twice before stripping tags. Missing
  this would have left literal `&lt;`/`&gt;` noise in every description.
- **Recruiter contact is never exposed.** Unlike Creative Circle's detail
  endpoint (`recruiterEmail`), the public Job Board API has nothing
  equivalent. `recruiter_name`/`recruiter_contact` are always `None` for
  Greenhouse jobs — an honest gap, not a parsing failure.
- **`sortType=relevance` has no server-side meaning here.** Creative
  Circle's relevance sort is a real ranking computed server-side; Greenhouse
  has none. `GreenhouseSource._relevance_key` computes its own crude
  title-weighted keyword-frequency heuristic client-side when a keyword is
  given and `sort=relevance`, and otherwise falls back to whatever order
  the API returned. This is explicitly a much weaker signal than Creative
  Circle's server ranking and isn't presented as equivalent.

No change was needed to `domain/job.py` (the canonical schema), `domain/query.py`,
`sources/base.py`, `storage/`, or `scoring/` to support Greenhouse — every
adaptation above lived entirely inside `sources/greenhouse/`, which is the
architecture working as intended.

## Rate limiting and retrieval hygiene

Same posture as Creative Circle (`sources/greenhouse/client.py`): honest
User-Agent, 15s timeout, exponential-backoff retry, 1-second minimum delay
between requests. No documented or observed rate limit — precautionary,
not a response to an observed throttle.

## Test strategy

- `tests/fixtures/greenhouse/*.json` — hand-built fixtures covering: the
  double-escaped HTML pattern, metadata-vs-location-text conflicts, a
  contract-type metadata label, a posting with no remote/employment
  signal at all, an embedded salary string, and a malformed item missing
  `id`.
- `tests/test_greenhouse_parser.py` — parsing/normalization, fully offline.
- `tests/test_source_greenhouse.py` — client-side filtering, pagination,
  sorting, board-not-found handling, health reporting — via
  `httpx.MockTransport`, fully offline.
- `tests/test_live_smoke_greenhouse.py` — hits the real API against the
  `anthropic` board, marked `@pytest.mark.live`, excluded by default. Run
  with `pytest -m live`.
