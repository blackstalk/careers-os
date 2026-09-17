# Source evaluation: adding Ashby and Lever (Phase 4)

Creative Circle (docs/sources/creative-circle.md) and the currently
configured Greenhouse boards (docs/sources/greenhouse.md) are unlikely to
surface enough Forward Deployed Engineer, Solutions Architect, Applied
AI, or ML Systems roles — Creative Circle is a creative/marketing/design
staffing brand, and the Greenhouse boards tracked so far weren't chosen
for AI-native coverage. This is a survey of what was investigated to fix
that, and why Ashby and Lever were chosen over LinkedIn/Indeed.

## LinkedIn — not viable

LinkedIn has no public search/read API. Its "Jobs API" exists only
inside the LinkedIn Partner Program, and that program is built for
partners *posting and managing* job listings (Talent Solutions/Recruiter
System Connect), not for querying LinkedIn's own job inventory. The only
way to read LinkedIn job search results programmatically is scraping an
authenticated session — brittle, ToS-violating, and dependent on a
personal LinkedIn login, all of which this project explicitly ruled out.

## Indeed — not viable

Indeed's Publisher API (the only path that ever allowed reading job
listings programmatically) closed to new publishers in October 2022 and
was fully shut down in 2023, replaced by an iframe-only hosted-search
widget (not data-accessible) and a separate NDA-gated enterprise data
partnership with six-figure minimums. No compliant, self-serve path
exists.

## Ashby and Lever — chosen

Both are public, unauthenticated, per-company JSON APIs — the same shape
as the existing Greenhouse adapter (docs/sources/greenhouse.md), and
explicitly intended by their vendors for exactly this kind of external
aggregation (Ashby's own docs name LinkedIn/Indeed/Built In/ZipRecruiter
as the intended consumers of its public endpoint).

Verified live during investigation (2026-09-15):

| Company | ATS | Total postings | Matching target-role keywords |
|---|---|---|---|
| OpenAI | Ashby | 808 | 73 (Applied AI Architect, Applied AI Engineer, Forward Deployed Engineer, Solutions Architect) |
| Ramp | Ashby | — | Applied AI Engineer confirmed present |
| Perplexity | Ashby | — | Applied AI roles confirmed present |
| WorkOS | Ashby | — | Applied AI Engineer confirmed present |
| Palantir | Lever | 316 | 77 (Forward Deployed AI/Software/Infrastructure Engineer, Deployment Strategist) |

Palantir is the company that coined "Forward Deployed Engineer" as a
title, which is why it's worth a dedicated Lever adapter even though
Lever's coverage for this project turned out to be much narrower than
Ashby's (`sierra`, `glean`, `harvey`, and `anduril` were checked and are
not on Lever). Ashby's roster skews toward exactly the AI-native company
segment this project is targeting and additionally exposes genuinely
structured compensation data (`docs/sources/ashby.md`) — a real
improvement over both existing sources, neither of which has a
structured salary field.

Also discovered along the way, at zero implementation cost: Scale AI's
careers page is backed by **Greenhouse** (confirmed live,
`boards-api.greenhouse.io/v1/boards/scaleai`, 224 postings) — addable to
`sources/greenhouse/boards.yaml` any time without touching code.

## What this did and did not change

Additive only, per the Phase 4 scope: two new `sources/<name>/` packages
implementing the existing `JobSource` contract (docs/architecture.md),
wired into the existing `jobs search`/`jobs discover`/`jobs run` source
lists exactly the way Greenhouse already is, plus a new `fde` search
profile (`career/data/search_profiles.yaml`) with keyword queries for
the target roles. No changes to `scoring/`, `career/pursue.py`,
`notifications/`, or the passive-mode alert threshold — new opportunities
from Ashby/Lever flow through the exact same discovery → eligibility →
qualification → transferable fit → career direction → opportunity value
→ pursue-worthiness → alert-policy pipeline as every other source, and
won't alert unless they clear the same `strong_pursue` bar everything
else has to clear.

## Phase 4.1: a fifth source

The first production runs showed the remaining gap was not volume but
relevance — specifically remote roles on both transition paths
(docs/discovery.md). Candidates were evaluated by live requests, not
from memory:

| Platform | Public, unauthenticated JSON? | Fits the per-company `JobSource` shape? | Verdict |
|---|---|---|---|
| **Workable** | Yes — `apply.workable.com/api/v1/widget/accounts/<slug>?details=true` | Yes | **Built** (docs/sources/workable.md) |
| SmartRecruiters | Yes — `api.smartrecruiters.com/v1/companies/<id>/postings` | Yes | **Not built** — see below |
| Workday | Only per tenant, with an opaque per-company site name that must be reverse-engineered from each careers page | No | Skip |
| iCIMS | Official API is licensed and employer-credentialed; the free path is HTML scraping | No | Skip |
| BambooHR | Yes — `<company>.bamboohr.com/careers/list` | Yes | Skip — inconsistent adoption, SMB-heavy, weak coverage for target roles |
| Teamtailor / Recruitee | Yes | Yes | Skip — EU-centric customer base |
| RemoteOK / Remotive / WeWorkRemotely | Yes (aggregator feeds) | No — cross-employer keyword feeds, a different kind of source | Possible future "aggregator" source category |

**Why Workable.** Same shape as Greenhouse/Ashby/Lever, no auth,
permissive `robots.txt`, a structured `telecommuting` flag, and it hosts
two directly relevant employers: Laravel (stack-adjacent) and Hugging Face
(target direction). Coverage is modest, and the doc says so.

**Why not SmartRecruiters, despite better target-role coverage.** It was
technically the strongest fit — confirmed live "Forward Deployed Solution
Engineer – Applied AI" postings at ServiceNow, 600+ open roles, a
structured `location.remote` flag, no auth. But
`api.smartrecruiters.com/robots.txt` disallows every user agent except
`LinkedInBot`, which is explicitly allowed on exactly the `/v1/companies/`
path this API lives on. That is a clear signal the endpoint is meant for a
named partner integration, not general third-party clients. This project's
bar for new sources is "accessible responsibly," so it is documented here
and deliberately not built. Revisit only if SmartRecruiters publishes a
general-access policy for the posting API.


## Phase 4.2: fixing the discovery bottleneck

**What the data showed.** On 2026-09-17 the database held 680 jobs. All
but 16 came from nine hand-listed companies (OpenAI 204, Anthropic 161,
Stripe 121, Palantir 67, then Ramp, Perplexity, Brex, Figma, WorkOS); the
other 16 were 11 Creative Circle jobs and 5 from the Laravel company's
Workable board. Only 53 (8%) were explicitly remote: 222 were hybrid, 163
onsite, and 242 didn't say. 636 of the 680 failed or needed verification
on eligibility, almost all because of work arrangement. The PHP, Laravel,
and WordPress profiles matched 19 jobs in total. The ATS adapters can only
search companies someone listed, and the listed companies mostly hire
in-office. The bottleneck was employer diversity and remote-first
coverage, not the number of adapters.

| Source | Access (verified 2026-09-17) | Decision |
|---|---|---|
| **Himalayas** | Free public JSON search API, no key; remote-only; full descriptions, structured location restrictions, salary, employment type, US filter; daily refresh; unpublished rate limit. Live US counts: Applied AI 292, Forward Deployed 177, Laravel 69, PHP 373, Backend Engineer 2,247 | **Built** (docs/sources/himalayas.md) |
| **More ATS boards** | Same Greenhouse/Ashby adapters; slugs checked live. Added GitLab, Grafana Labs, Twilio, LaunchDarkly, Samsara, Fivetran (Greenhouse); Supabase, PostHog, Linear, Replit, Cohere, LangChain, Baseten, Deepgram, ElevenLabs, n8n, Zapier, Render, Railway, Pinecone (Ashby) | **Built** (config only) |
| JSearch (RapidAPI, Google for Jobs data) | Paid key; covers LinkedIn, Indeed, ZipRecruiter, Glassdoor and others with full descriptions and a remote flag; free tier plus paid plans from roughly tens of dollars a month | **Deferred, needs an owner decision.** The only legitimate route to LinkedIn and Indeed volume. Would plug in as a second aggregator (`sources/authority.py`), with its key in a repo secret |
| Indeed | Publisher API shut down 2023; remaining APIs are partner-only (employers, agencies, ATS platforms) | Rejected |
| LinkedIn | Partner-only posting APIs; no search API | Rejected |
| ZipRecruiter | Partner/publisher APIs require an approved partnership | Rejected |
| CareerBuilder | Legacy developer-key API (now under Monster); no self-serve access found | Rejected |
| Workday | Per-tenant endpoints with opaque, per-company site names | Rejected: no generic adapter possible |
| SmartRecruiters | Public posting API, but `robots.txt` allows only LinkedInBot on that path | Rejected (see Phase 4.1) |
| Adzuna | Free key (~1,000 calls/month); truncated description snippets; no remote filter | Deferred: snippets would hide hard requirements from qualification |
| Hacker News "Who is Hiring" (Algolia API) | Free; ~400 posts/month, relevant, remote-heavy, but unstructured free text | Deferred to Phase 4.3: needs careful parsing |
| Remotive / RemoteOK | Public, low volume, restrictive terms, largely overlaps Himalayas | Rejected for now |

### Aggregators vs. authoritative sources

`sources/authority.py` marks Himalayas as an aggregator. Everything else
(the ATS boards and Creative Circle) publishes the posting itself. The
scheduled run acts on high-confidence cross-source duplicates only (same
normalized company and title):

- an aggregator copy is `superseded` when the employer's own posting was
  found in the same run;
- a job isn't emailed if any duplicate was already emailed;
- a job isn't emailed if any duplicate was applied to
  (docs/applications.md).

Description-similarity-only flags are still recorded for review but never
drive these decisions: two different jobs can share boilerplate text.
Records are never merged, so provenance stays intact.

### Scaling fixes that came with more volume

- The duplicate scan compared every new job's description against every
  job from other sources. It now uses an in-memory index and only runs the
  expensive comparison when the normalized title, normalized company, or
  a description fingerprint already matches.
- ATS adapters downloaded the whole board once per query; they now
  download it once per run.
- A job found by several queries is scored once per run.
- `jobs run` prints a per-source table: raw results, new jobs, unique jobs,
  jobs no other source found, eligible, qualified, pursue or better,
  alert-ready, and any failures.
