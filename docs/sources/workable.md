# Source: Workable (public job-widget API)

Investigated 2026-09-16 as part of Phase 4.1 (see docs/source-evaluation.md
for the full comparison against SmartRecruiters, Workday, iCIMS, BambooHR,
and others).

## How discovery works

- `GET https://apply.workable.com/api/v1/widget/accounts/{account}?details=true`
  — every published job for one company account, in one response.

This is the unauthenticated endpoint behind Workable's embeddable careers
widget, which companies paste into their own sites. It is a different
product from Workable's authenticated v3 HR API (employer-issued token),
which this project never uses. `apply.workable.com/robots.txt` disallows
nothing for `User-agent: *`.

Verified directly:

- Without `details=true`, jobs carry no description at all. With it, each
  job includes full HTML `description`.
- There is **no per-job detail endpoint** —
  `/accounts/{account}/jobs/{shortcode}` returns 404. `fetch_job`
  re-lists the account and finds the shortcode, same approach as Ashby.
- The response's top-level `name` is the company display name; jobs
  themselves carry no company field.

## Adapter shape

Identical to `AshbySource`: one `WorkableSource` per account slug
(`sources/workable/accounts.yaml`), `NormalizedJob.source` is always the
constant `"workable"`, and the account slug lives in `source_job_id`
(`"<account>:<shortcode>"`). All filtering is client-side.

## Field mapping and honest gaps

- **Remote status**: `telecommuting: true` → `remote`. `telecommuting:
  false` only means "not marked remote" — it cannot distinguish hybrid
  from onsite, so it maps to `unknown`, never guessed either way (see
  docs/eligibility.md, "A note on `unknown` work arrangement").
- **Location**: city/state/country joined; telecommuting postings get a
  `(Remote)` suffix (e.g. `Paris, Île-de-France, France (Remote)`). This
  is what lets the existing remote-location geography eligibility check
  flag country-scoped remote roles as `verify`, exactly as it does for
  Ashby/Lever's `"India - Remote"` style strings.
- **Employment type**: free text (`Full-time`, `Contract`, ...), scanned
  like Lever's `commitment`.
- **Compensation**: no structured field; best-effort regex over the
  description text, same low-confidence approach as Greenhouse/Lever.
- **Dates**: plain `YYYY-MM-DD`, treated as midnight UTC.

## Coverage — honest assessment

Modest. There is no directory of Workable customers, and many plausible
company slugs return 404 or an account with zero open roles. The default
account list is small and chosen for the two transition paths
(docs/discovery.md), not for volume:

| Account | Why | Open jobs at investigation |
|---|---|---|
| `laravel` | Stack-adjacent — the Laravel company itself | 5 (all remote) |
| `huggingface` | Target direction — applied AI / ML systems | 6 (all remote, US + EMEA variants) |
| `tighten` | Stack-adjacent — Laravel consultancy | 0 |
| `10up` | Stack-adjacent — WordPress agency | 0 |

Accounts with zero current openings cost one request per run and are
kept in case they post.

## Rate limiting and retrieval hygiene

Same posture as the other adapters: honest User-Agent, 15s timeout,
exponential-backoff retry, 1-second minimum delay between requests.

## Test strategy

- `tests/fixtures/workable/account_response.json` — remote/non-remote,
  US/non-US, contract, embedded salary text, HTML entities, and a
  malformed item missing `shortcode`.
- `tests/test_workable_parser.py` — parsing/normalization, offline.
- `tests/test_source_workable.py` — client-side filtering, `details=true`
  request param, not-found handling, `fetch_job` re-list behavior, via
  `httpx.MockTransport`.
- `tests/test_live_smoke_workable.py` — real API against `huggingface`,
  `@pytest.mark.live`, excluded by default.
