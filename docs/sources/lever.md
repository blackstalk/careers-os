# Source: Lever (public Postings API)

Investigated 2026-09-15 against Palantir's public job board as part of
Phase 4's discovery expansion (docs/alerts.md, docs/architecture.md) —
Palantir is the company that coined the term "Forward Deployed Engineer"
and, at investigation time, had 316 open postings on its Lever board, 77
of which matched Forward Deployed/Solutions/Applied AI title keywords.

## How discovery works

A public, unauthenticated JSON API Lever customers use to power their
own careers pages (no single official reference page the way Greenhouse
has, but the shape is stable and widely relied upon for exactly this
purpose — see docs/source-evaluation.md for the survey that found it).

- `GET https://api.lever.co/v0/postings/{company}?mode=json` — every
  open posting for a company, in one response (a **bare JSON array**,
  not an object wrapping a `jobs`/`postings` key — the one structural
  difference from Greenhouse/Ashby's response shape).
- `GET https://api.lever.co/v0/postings/{company}/{postingId}?mode=json`
  — one posting's detail. Unlike Ashby, this endpoint exists, so
  `LeverSource.fetch_job` hits it directly, same as Greenhouse.

Both unauthenticated.

### Multi-tenant, per-company slugs

A company slug (e.g. `palantir`) identifies which company's postings you
get, mirroring Greenhouse/Ashby's per-tenant pattern. Confirmed live:
`palantir`. Several other AI-infrastructure companies with Forward
Deployed/Solutions-style roles were checked and are **not** on Lever
(`sierra`, `glean`, `harvey`, `anduril` all 404) — Lever's real strength
for this project is concentrated in Palantir specifically, not broad
coverage the way Ashby's roster is.

## Adapter shape (why one instance = one company)

`LeverSource` is constructed with a `company` slug and only ever talks
to that one company — see `sources/lever/source.py`. Searching several
companies means instantiating several `LeverSource`s (see `jobs search
lever-all`, which loops over `sources/lever/companies.yaml`), identical
to `GreenhouseSource`/`AshbySource`. `NormalizedJob.source` is always the
constant `"lever"` (never `"lever:<company>"`) for the same cross-source
`(source, source_job_id)` dedup reason documented in
docs/architecture.md — the company slug lives inside `source_job_id`
(`"<company>:<id>"`), which is also what lets `fetch_job` know which
per-company detail endpoint to hit from a bare id string.

## What's different from Greenhouse/Ashby (fourth-source validation)

- **Bare array response, not an object wrapper.** `LeverClient.list_postings`
  returns `list[dict]` directly rather than unwrapping a `jobs` key —
  the one genuinely different response shape among the four sources.
- **Structured `workplaceType`, unstructured everything else.** Like
  Ashby, `workplaceType` (`remote`/`hybrid`/`onsite`) is a real top-level
  field when present, checked before falling back to scanning
  `categories.location` text for "remote" — same heuristic Greenhouse
  uses as its only signal. Unlike Ashby, employment type (`categories.
  commitment`) is free text set per-company ("Full-time", "Contract",
  "Internship" observed on Palantir's board), so `detect_employment_type`
  scans it the same way Greenhouse scans metadata labels — no fixed enum
  to rely on.
- **Compensation is usually free text, not structured** — the opposite
  of Ashby. Palantir's postings embed it in a separate `additionalPlain`
  field (a distinct "Salary" section, e.g. *"The estimated salary range
  for this position is estimated to be $135,000 - $200,000/year"*)
  rather than inside the main `descriptionPlain`. `extract_salary_from_text`
  scans both fields combined with a regex pattern, same low-confidence,
  best-effort approach as Greenhouse's `SALARY_TEXT_PATTERN` — and, like
  Greenhouse, not every posting has it (many Palantir postings have no
  salary text at all).
- **No company-name or recruiter-contact field per job**, same fallback
  (`NormalizedJob.company` = the company slug) already used for
  Greenhouse/Ashby when a display name isn't published.
- **`createdAt` is an epoch-millisecond integer**, not an ISO string —
  the only source with this representation; `parser.parse_job_payload_to_raw_job`
  converts it to an ISO string immediately so everything downstream of
  `RawJob` sees the same string-typed `raw_posted_date` shape regardless
  of source.

No change was needed to `domain/job.py`, `domain/query.py`,
`sources/base.py`, `storage/`, or `scoring/` to support Lever — every
adaptation above lives entirely inside `sources/lever/`.

## Rate limiting and retrieval hygiene

Same posture as the other adapters (`sources/lever/client.py`): honest
User-Agent, 15s timeout, exponential-backoff retry, 1-second minimum
delay between requests. No documented or observed rate limit —
precautionary, not a response to an observed throttle.

## Test strategy

- `tests/fixtures/lever/postings_list_response.json` — hand-built
  fixture (a bare JSON array, matching the real response shape) covering:
  a posting with salary text in `additionalPlain`, a remote posting via
  `workplaceType`, a contract posting via `categories.commitment`, a
  posting with neither remote nor compensation signal, and a malformed
  item missing `id`.
- `tests/test_lever_parser.py` — parsing/normalization, fully offline,
  including the epoch-millisecond date conversion and the
  description/salary-text separation (the combined text used for salary
  extraction must never leak into the normalized `description` shown to
  the user).
- `tests/test_source_lever.py` — client-side filtering, pagination,
  sorting, company-not-found handling, direct `fetch_job` detail-endpoint
  behavior, health reporting — via `httpx.MockTransport`, fully offline.
- `tests/test_live_smoke_lever.py` — hits the real API against the
  `palantir` board, marked `@pytest.mark.live`, excluded by default. Run
  explicitly with `pytest -m live`.
