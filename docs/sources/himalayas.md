# Source: Himalayas (remote-jobs aggregator)

Added in Phase 4.2 to fix the discovery bottleneck: before it, almost every
discovered job came from nine hand-listed companies, only 8% of them remote.
See docs/source-evaluation.md for the comparison that led here.

## How it works

- `GET https://himalayas.app/jobs/api/search?q=<keyword>&country=US&sort=recent&page=<n>`
  ([API docs](https://himalayas.app/docs/remote-jobs-api)).
- Free, no key. Real server-side keyword search across thousands of
  employers. Every listing is remote.
- `country=US` returns postings open to US-based applicants: US-restricted,
  worldwide, or multi-country lists that include the US.
- 20 results per page. Data refreshes once a day upstream, so one scheduled
  run per day loses nothing.

`HimalayasSource` (`sources/himalayas/`) is a single keyword source like
Creative Circle. It is not one instance per company like the ATS adapters.
Each profile query fetches at most `PAGES_PER_QUERY` pages (2, so 40
results), newest first. Broad queries such as "Backend Engineer" match
thousands of postings, and scoring all of them every run would be slow and,
with the AI layer on, costly.

## Normalization

| Field | From |
|---|---|
| `source_job_id` | `<companySlug>:<job slug>` from `guid` (a stable job URL) |
| `company` | `companyName` |
| `remote_status` | always `remote` |
| `location` | `locationRestrictions` joined as `"<countries> - Remote"`, or `"Remote (Worldwide)"` when empty |
| `employment_type` | `employmentType` (Full Time / Part Time / Contractor / Temporary / Intern) |
| salary / hourly | `minSalary`/`maxSalary`, split by `salaryPeriod`, USD only |
| `description` | full HTML `description`, tags stripped |
| `source_url` | `applicationLink` (the Himalayas job page, not the employer's ATS) |

The location string reuses the existing remote-location eligibility check:
- a list that includes the United States is eligible;
- a list naming only other countries is `verify`;
- worldwide is eligible.

A page can list the same posting twice, so results are de-duplicated by id.

## Authority and duplicates

Himalayas is an **aggregator** (`sources/authority.py`). When the same job
is also found on an employer's own board in the same run (same company and
title, a high-confidence duplicate flag), the aggregator copy is marked
`superseded` and never alerts. The employer posting's evaluation decides,
so a "Remote" aggregator listing can't override a hybrid or
domain-restricted employer posting. Provenance is kept: both records and
the duplicate flag stay in the database.

## Source health

`HimalayasClient` raises typed errors, and each one shows up in
`jobs run`'s per-source table and in `jobs health himalayas`:

| Situation | Reported as |
|---|---|
| HTTP 429 (retried with backoff first) | `rate_limited` |
| Response isn't JSON, or has no `jobs` list | `schema_changed`: fails loudly instead of returning zero jobs |
| Other HTTP errors / network failures | `request_failed` |
| Individual job can't be parsed | counted as a parse failure (`parse_failed`); the rest of the page still loads |
| Valid response with zero matches | healthy, with a note |

No authentication, so there is no "configuration missing" state.

## Terms and limits

- The rate limit isn't published. The client waits 1 second between
  requests and backs off on 429.
- Attribution is only required when the data is displayed publicly. Careers
  OS is a private tool.
- `robots.txt` allows the API paths.
- `fetch_job` has no detail endpoint to call; it re-searches by company and
  matches the id.

## Tests

- `tests/test_source_himalayas.py`: parser, paging cap, early stop, de-dup,
  filters, typed errors, and eligibility of the location strings (offline,
  `httpx.MockTransport`).
- `tests/test_live_smoke_himalayas.py`: real API, `pytest -m live`.
