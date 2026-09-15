# Source: Creative Circle (Everforth candidate portal)

Portal: `https://candidateportal.creativecircle.com/`
Search page: `https://candidateportal.creativecircle.com/search`

Investigated 2026-09-14 by inspecting the live site's network traffic and
its own client-side JavaScript bundle. Everything below reflects what was
actually observed, not documentation the site publishes — **there is no
published/versioned public API contract here**, and this adapter should be
treated as coupled to an implementation detail that could change without
notice.

## How discovery actually works

The search page (`/search`) is a client-side rendered single-page app (a
Vite/Vue bundle — the server returns an ~1.9KB HTML shell with a `<div
id="app">` and a `<script type="module" src="/assets/index.*.js">`; there
is no server-rendered HTML and no embedded structured JSON to scrape).

The SPA calls a JSON API on the same origin:

- `GET /ccv5-jobs/search` — job search / listing
- `GET /ccv5-jobs/details?id={id}&buid=3` — full detail for one job
- `GET /ccv5-jobs/autocomplete?search={text}&buid=3` — keyword autocomplete
  (not used by this adapter)

This is an **undocumented but public, unauthenticated JSON API** — option
2 from the investigation checklist, not option 1 (no published API docs
exist) and not option 3/4 (no server-rendered HTML or embedded JSON to
parse). Concretely: no cookies, auth headers, or session state are
required — a bare `curl` from a fresh shell gets identical results to the
browser. `robots.txt` (see below) explicitly allows crawling `/search` and
`/job-detail/`, and even carves out allow-rules for AI crawlers by name
(including Anthropic's), which is a reasonable signal that publicly
surfacing this listing data is an intended use, not something being
defended against — but that allowance is about crawling rendered pages,
not a statement that the underlying JSON endpoints are a stable contract.
We chose to call the JSON endpoints directly anyway, rather than fetch and
scrape the rendered page, because: no HTML parsing to maintain, no
JS-rendering step required, and identical/richer data (the rendered page
gets its data from exactly this API).

### Shared platform

`candidateportal.creativecircle.com`'s `robots.txt` literally begins
`# Robots.txt for CyberCoders`, and a response header on `/search` shows
`X-Upstream: joblistings-prod-b.apexsystems.net`. Reading the bundle
confirms this candidate portal is shared, white-labeled infrastructure
across multiple ASGN staffing brands, selected by a `buid` (business unit
id) query parameter:

| `buid` | Brand |
|---|---|
| 1 | CyberCoders |
| 2 | Apex Systems |
| 3 | **Creative Circle** (what this adapter uses) |

This adapter only ever sends `buid=3`. It's documented here because it
explains why the same API structure would trivially support additional
ASGN-family source adapters later, and because it's a real fact about
whose infrastructure this data is coming from.

## robots.txt

```
User-agent: *
Disallow: /apply
Disallow: /register
Disallow: /resume-collector
Disallow: /profile
Disallow: /member-onboarding
Disallow: /account
Disallow: /admin
Allow: /
```

`/search` and `/job-detail/` are explicitly allowed (and separately
allow-listed for named AI crawlers). This adapter never touches any of the
disallowed paths (`/apply`, `/register`, `/profile`, `/account`, etc.) —
Phase 1 is read-only job discovery, nothing that would require
authentication or touch a candidate's account.

## Endpoints used

### `GET /ccv5-jobs/search`

Query parameters, confirmed by direct testing against the live API (not
just reading the bundle — several assumptions from reading the JS turned
out to be wrong, see "Known limitations" below):

| Param | Values | Server-side? | Notes |
|---|---|---|---|
| `buid` | `3` | — | selects Creative Circle |
| `rows` | int | yes | page size |
| `page` | int | yes | 1-indexed |
| `sortType` | `relevance` \| `date` | yes | confirmed both work |
| `daysPosted` | `0`, `1`, `3`, `7`, `14` | yes, approximately | see limitations |
| `keyword` | free text | yes | matches title/description |
| `locationKeyword` | free text | yes | matches city/state |
| `workLocationTypeId` | `1`=onsite, `2`=hybrid, `3`=remote | yes, confirmed | used for `--remote` |
| `termOption`, `filtertype`, `positionTypeId` | — | **no** | tested, no effect — see limitations |

Response shape:

```json
{
  "numFound": 345,
  "jobs": [
    {
      "Id": "939928", "jobId": "AS34-1988749",
      "jobTitle": "...", "JobURL": "social-content-creator-job-939928",
      "SalaryType": "Hourly", "DatePost": "2026-06-04T13:20:07.377Z",
      "City": ["Wilton"], "StateCode": ["CT"],
      "TaxTerm": "CONT", "RecruiterName": "...",
      "ShortDescription": "...", "WorkLocationTypeId": 1,
      "CompanyName": "Melissa & Doug",
      "HourlyMin": 30, "HourlyMax": 35,
      "SalaryMin": null, "SalaryMax": null
    }
  ]
}
```

### `GET /ccv5-jobs/details?id={id}&buid=3`

Full detail for one job — full HTML `description`, `recruiterEmail`,
`dateCreated`/`dateModified`. Field names are **camelCase here vs.
PascalCase on search results** (`taxTerm` vs `TaxTerm`, `workLocationTypeId`
vs `WorkLocationTypeId`, etc.) — `parser.py`'s `_FIELD_ALIASES` maps both
shapes onto one internal name so the rest of the adapter doesn't care
which endpoint a payload came from.

**Known gap**: the detail endpoint does **not** return a company name
field at all (confirmed: `CompanyName` appears on search results but has
no equivalent anywhere in `/ccv5-jobs/details` — see
`tests/test_creative_circle_parser.py::test_company_name_absent_on_detail_response`).
Company name is only available from the search result. Phase 1's pipeline
normalizes from search results directly for this reason (see "Design
decision" below).

## Field mapping

| Canonical field | Creative Circle source | Notes |
|---|---|---|
| `title` | `jobTitle` | |
| `company` | `CompanyName` (search only) | absent on detail responses |
| `location` | `City`+`StateCode` (search) or `locationsExactSplit` (detail) | often empty for remote roles — genuine data gap, not a bug |
| `remote_status` | `WorkLocationTypeId`/`workLocationTypeId` | 1=onsite, 2=hybrid, 3=remote |
| `employment_type` | `TaxTerm`/`taxTerm` | `PERM`->full_time, `CONT`->freelance (matches the site's own `getJobType()` labeling — it does not distinguish contract vs. freelance) |
| `salary_min`/`salary_max` | `SalaryMin`/`SalaryMax` (search) or `salaryMin`/`salaryMax` (detail) | never derived from hourly |
| `hourly_min`/`hourly_max` | `HourlyMin`/`HourlyMax` (search) or `hourlyMin`/`hourlyMax` (detail) | never derived from salary |
| `skills` | `tags` (search) or `tagsSplit` backtick-joined (detail) | |
| `posted_at` | `DatePost` (search) or `dateCreated` (detail) | |
| `updated_at` | `dateModified` (detail only — not present on search results) | |
| `recruiter_name` / `recruiter_contact` | `RecruiterName` / `recruiterEmail` (detail only) | |

A job can have salary only, hourly only, both, or neither — all four cases
are exercised in `tests/fixtures/creative_circle/search_response.json` and
asserted in `test_creative_circle_parser.py`. Compensation fields are
never cross-converted (e.g. no salary-from-hourly estimate).

## Pagination

Standard `page`/`rows`, with `numFound` in the response giving the total.
No documented upper bound was found; this adapter self-imposes
`MAX_ROWS = 50` per request (see `constants.py`) as a courtesy cap, not
because the server enforces one.

## Known limitations (discovered by testing, not assumed)

- **Employment-type filtering is not honored server-side.** The site's own
  JS builds `termOption`/`filtertype`/`positionTypeId` params for this, but
  sending any of them to `/ccv5-jobs/search` had zero effect on results in
  direct testing — identical `numFound` and identical top results with
  `termOption=PERM` vs `termOption=CONT` vs omitted. This adapter filters
  `employment_type` **client-side** after fetching (see
  `CreativeCircleSource.search`), using the `TaxTerm` field, which *is*
  reliable per-record.
- **`daysPosted` semantics are approximate.** `daysPosted=1` ("Just
  Posted") returned some jobs whose `DatePost` was over a week old in
  testing. This adapter treats the server param as a coarse pre-filter
  only and **always enforces the exact requested cutoff client-side**
  against the parsed `DatePost`/`dateCreated` (see `_within_posted_window`
  in `source.py`) — so `--days N` is accurate regardless of what the
  server's bucket actually matches on internally.
- **Company name is unavailable from the detail endpoint** (see above).
- **Location is frequently empty for remote listings** — many remote jobs
  ship `City: []`, `StateCode: []`. Normalized `location` is `None` in
  that case rather than a guess.
- **Creative Circle's catalog skews creative/marketing/design.** Searching
  for engineering-heavy terms like "solutions architect" or "forward
  deployed engineer" frequently returns zero results — this is a real
  property of what Creative Circle staffs for, not a broken adapter. The
  adapter surfaces a note (via `SourceHealth.notes`) when a keyword/location
  search returns zero results, precisely so this doesn't read as silent
  failure.

## Design decision: search results are the primary ingestion payload

`CreativeCircleSource.search()` normalizes directly from
`/ccv5-jobs/search` results rather than following up with a
`/ccv5-jobs/details` call per row. Reasons:

1. Search results already carry everything the canonical schema needs
   except the full HTML description (title, company, location, comp,
   dates, recruiter name, tags) — `ShortDescription` is a reasonable stand-in
   for `description` at ingestion time.
2. Fetching detail for every search hit would multiply request volume
   (N+1) against an undocumented endpoint with no stated rate limit —
   contrary to the "responsible retrieval" requirement.
3. `fetch_job(source_job_id)` remains available (used by `jobs show`) for
   on-demand full detail when a human is actually looking at one job.

## Rate limiting and retrieval hygiene

Implemented in `sources/creative_circle/client.py`:

- Honest `User-Agent` identifying this as a personal job-search tool with a
  contact address (`constants.USER_AGENT`).
- 15s request timeout.
- Exponential-backoff retry (up to 3 attempts) on transport errors and
  HTTP error statuses, via `tenacity`.
- A minimum 1-second delay enforced between consecutive requests
  (`_RateLimiter` in `client.py`), regardless of how fast the caller wants
  to go.
- Self-imposed `rows` cap (see above).

No documented rate limit was found and none was hit during investigation
(a handful of sequential test requests all returned in under a second with
no throttling headers or 429s observed) — the above is precautionary, not
a response to an observed limit.

## Test strategy

- `tests/fixtures/creative_circle/*.json` — realistic, hand-built fixtures
  (not captures of real postings) covering: salary-only, hourly-only, both,
  neither; full-time and freelance; remote/hybrid/onsite; missing
  `Id` (malformed); null title; unrecognized `TaxTerm`/`WorkLocationTypeId`;
  unparseable date.
- `tests/test_creative_circle_parser.py` — parsing and normalization, fully
  offline.
- `tests/test_source_creative_circle.py` — adapter behavior (query mapping,
  client-side employment-type filter, client-side days-posted cutoff,
  health reporting) using `httpx.MockTransport`, fully offline.
- `tests/test_live_smoke.py` — hits the real API, marked `@pytest.mark.live`
  and excluded from the default run (`addopts = "-m 'not live'"` in
  `pyproject.toml`). Run explicitly with `pytest -m live`.
