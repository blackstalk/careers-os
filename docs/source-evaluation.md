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


## Indeed (re-checked October 2026)

**Not implemented, deliberately.** Indeed's partner documentation shows a
GraphQL `jobSearch` example, but access to the underlying
job-retrieval-service is provisioned per partner and the documented
ecosystem is oriented toward employer and ATS integrations, not personal
job discovery. Nothing verifiable here grants this project API access, so
no adapter was built and no scraping was added.

**What exists instead:** `jobs add-posting` (ingestion/manual.py). Supply
the posting URL and the copied description and it runs through the same
normalize, persist, score, evaluate path as every adapter:

```bash
command jobs add-posting "https://www.indeed.com/viewjob?jk=..." \
    --file posting.txt --title "Senior Laravel Developer" --company Acme \
    --canonical-url "https://job-boards.greenhouse.io/acme/jobs/123"
```

The job is stored under `source="manual"` with a URL-derived id, so
re-adding the same URL updates that row instead of duplicating it. When
`--canonical-url` is supplied it becomes the stored posting URL; when the
employer's ATS posting is later discovered on its own, the existing
cross-source duplicate scan links the two and `sources/authority.py`
prefers the employer copy. Nothing is merged or overwritten.

## Source coverage is biased toward the exploratory track

Measured against the local database (October 2026), not estimated:

| Discovery profile | Stored jobs surfaced |
|---|---|
| fde (exploratory) | 1,540 |
| backend_platform (now split) | 601 |
| php | 68 |
| wordpress | 53 |
| laravel | 46 |
| craft | 2 |

The configured boards explain it: of 34 boards, the Greenhouse and Ashby
lists are AI labs and developer-infrastructure companies (OpenAI,
Anthropic, Stripe, ElevenLabs, GitLab, Baseten, LangChain, Cohere,
Deepgram...), and only three accounts across all sources target
PHP/Laravel/WordPress work. Craft CMS has effectively no coverage: two
jobs in the entire database.

**No boards were added in this phase.** Adding companies from memory
would be guessing, and every existing board was verified live before
being added. Expanding replacement-track coverage is the single highest
leverage follow-up, and it needs a verifiable list of Craft/Laravel
agencies and product companies with supported ATS boards, checked slug by
slug against the adapters before being committed. Until then the
replacement track depends mostly on Himalayas keyword search and manual
postings.


## Phase 6: replacement-track source coverage

Two paths were investigated, neither of them by scraping a job board:

- **Path A** — mine the stack-profile jobs already in the database and
  resolve their employers' ATS destinations from stored URLs and
  payloads. This established one hard fact: Himalayas raw payloads carry
  no ATS link at all (`applicationLink` points back to the Himalayas
  page), so an employer discovered through the aggregator gives no free
  route to its own board. Every board below had to be established from
  public evidence instead.
- **Path B** — the public Craft CMS and Laravel partner directories used
  strictly as *company-discovery* sources, never as job sources. Appearing
  in a directory earned a company nothing; it still had to have a
  verifiable board on a supported ATS and useful remote-US engineering
  coverage.

### Verification method, and a false-positive trap

Slugs were never guessed. Each candidate was queried against the ATS's
own public API, and **the ATS had to confirm the board's owner**:
Greenhouse (`/v1/boards/<slug>` returns `name`) and Workable
(`widget/accounts/<slug>` returns `name`) both do, and that name had to
share a distinctive token with the candidate or the hit was discarded.

This matters because the first probe pass truncated company names to
their first word and produced entirely bogus matches: `block` → Block
Inc, `pine` → Pine, `mighty` → Mighty (legal tech), `iris` → Iris North
America, `apex` → Apex Eye, `paradigm` → Paradigm. `64robots` likewise
resolves to an unrelated Workable account ("Doğanlı Teknoloji"). None of
those are real coverage. Greenhouse 404s an unknown board, but Workable
answers 200 for an account that exists with zero postings, so
"responded" alone is never evidence of identity.

### Added (verified live 2026-10-07)

| Employer | ATS | Slug | Total | Remote | Remote-US | Relevant eng | Compensation | Already via Himalayas | Why |
|---|---|---|---|---|---|---|---|---|---|
| RevStar | Workable | `revstar` | 84 (46 US) | all flagged remote | 46 US | 2 stack + 4 FDE/SA distinct | none published | 5 jobs | Only verified board covering both tracks: Full Stack Web Developer and Software Development Lead alongside Forward Deployed Engineer (AWS/Databricks/DevOps) and Solutions Architect |
| Upstart | Greenhouse | `upstart` | 90 | 84 | 84 | 25 | **25 of 25 relevant roles carry a structured range; 24 have a minimum at or above $120,000** | 7 jobs | Highest remote-US density verified, and complete compensation on every relevant engineering role — the opposite of the Coinbase case |

### Rejected, with reasons

| Employer | Status | Why not |
|---|---|---|
| **Coinbase** | greenhouse `coinbase` verified: 225 jobs, 135 remote-US, 62 relevant | **Adding it would lose information.** Coinbase's own Greenhouse postings publish no pay; the Himalayas copies carry `$186,065-$218,900`. Because the aggregator copy is superseded when an employer copy is found in the same run (`ingestion/scheduled_run.py`), those roles would drop from `confirmed_above` to `unknown` — out of the confirmed alert lane and into the budget-1 verification lane. A larger inventory bought with worse compensation data is a net loss against the replacement objective |
| JFrog | greenhouse `jfrog` verified: 66 jobs | Only 7 remote / 6 remote-US and 2 relevant; already present via Himalayas |
| Mission Lane | greenhouse `missionlane` verified: 16 jobs, all remote | Zero stack-relevant engineering roles |
| lemon.io | ashby `lemon-io` verified: 4 jobs | A freelancer marketplace: its own ATS lists Head of Engineering, BDR, AE. Its developer work never reaches its board |
| Curotec, Tarteel AI, First Due, Happy Cog, SunnyByte, Foster Commerce, Click Rain, Sourcetoad, Kirschbaum, exolnet, SimplyPHP, Pixel & Tonic, nystudio107, Oomph, Lullabot, Four Kitchens, Postlight, Catalyst Mobility, DecisionPoint, Peloton, and 9 more | No board on any supported ATS | 29 candidates have no Greenhouse/Ashby/Lever/Workable presence. Happy Cog and Curotec post on their own sites, which is why Himalayas is the only way they reach CareersOS. Nothing to add without an unsupported adapter |

### Dormant stack employers: verified boards, zero openings

The most useful Path B result is a negative one. These Craft/Laravel
agencies have **verified, live Workable accounts that were serving zero
open postings** on 2026-10-07:

Vehikl, Threadable, Brilliance, Crowd Favorite, Thunk, Bluehouse Group,
Steadfast Collective, Viget, Bonfire, Barrel, Supercool, Mindsize,
Fortnight, Elevated Third, Fuzzy Math, Big Cartel, Chromatic, Matrix
Internet, Rareloop, Simple, GoodWork — plus Order.co, Clera, Lumen
Technologies, Peraton and Toptal from Path A, and `tighten` which was
already configured.

The Craft/Laravel agency ecosystem is therefore real and reachable, but
not hiring. **None were added**: 27 boards would cost 27 requests on
every scheduled run to contribute zero opportunities — the same pattern
`sources/workable/accounts.yaml` already records for `tighten` and
`10up`. The list is preserved here as research so these employers can be
revisited without re-running the investigation. See the backlog note on
dormant stack-employer monitoring below.

### Configured-ATS share is not a quality metric

Phase 6 was planned on the assumption that employer ATS copies are
richer than aggregator copies and that raising the configured-ATS share
of stack-profile jobs (then ~26%) toward something like 50% would mean
better coverage. **The live evidence contradicted that, so the goal was
dropped.** Measured on one production-equivalent run:

| Source | Jobs | needs_verification | immediate_fit | pursue or better |
|---|---|---|---|---|
| himalayas | 549 | 50% | 68 | 213 |
| greenhouse | 576 | 61% | 30 | 47 |
| ashby | 591 | 32% | 14 | 39 |

Himalayas produced more `immediate_fit` and more `pursue`-or-better
opportunities than both configured ATS sources combined, and parsed
*better* than Greenhouse. Coinbase is the sharpest case: the aggregator
preserves compensation the employer's own board omits.

Compensation completeness varies by *employer*, not by source category —
which is the point. On the same ATS, Coinbase publishes no pay at all
while Upstart publishes a structured range on every relevant engineering
role. A board is worth adding when that employer's own postings are
good, never because adding boards raises a share. Share of jobs
coming from configured boards measures configuration, not opportunity
quality, and optimizing it would have argued for adding Coinbase — the
one decision the evidence most clearly rejects.

The metrics that actually bear on source value:

- credible replacement opportunities discovered;
- `immediate_fit` count;
- confirmed and verification alert candidates;
- unique relevant opportunities contributed by each source (jobs no
  other source found);
- compensation completeness;
- duplicate versus incremental coverage;
- request cost per useful opportunity produced.

`jobs run` already prints most of these per source (see "Scaling fixes
that came with more volume" above).

Where coverage genuinely is thin is the core stack: only **18%** of
Craft/PHP/Laravel/WordPress jobs come from a configured ATS (105 of 128
are Himalayas). Phase 6 established that this is not a discovery failure
— those agencies have verifiable boards and simply are not hiring. Note
also that 110 of the 549 Himalayas stack jobs come from staffing and
outsourcing reposters (Bright Vision, NightOwl, Xperteez, Hivex,
PradeepIT, Lifelancer), so part of that volume is low quality.

## Backlog (not implemented)

### Cross-source field provenance / enrichment

`sources/authority.py` plus the supersession step in
`ingestion/scheduled_run.py` currently make an all-or-nothing choice: when
the employer's own posting is found, the aggregator copy is dropped from
alert consideration entirely. Coinbase shows the cost — the discarded copy
may hold the only compensation data for that job.

A canonical employer copy should eventually be able to coexist with
useful fields from an aggregator copy (compensation first, possibly
location restrictions) without merging records or losing provenance: the
employer copy stays authoritative for the posting, while a per-field
record of where each value came from allows a richer aggregator field to
fill a gap the employer copy leaves empty. Records must still never be
silently merged, and a field taken from an aggregator must remain
visibly attributed to it so compensation certainty
(`domain/compensation.py`) can weigh it accordingly.

### Dormant stack-employer monitoring

The Phase 6 investigation produced a verified set of Craft/Laravel
agencies with supported ATS accounts and no current openings (listed
above). Querying all of them on every scheduled run is the wrong trade:
27 extra requests per weekday for zero opportunities.

Worth considering later: a cheaper periodic mechanism — a separate
low-frequency check (weekly, or its own workflow) that asks only whether
any dormant board has become non-empty, and promotes an employer into the
normal source list when it starts hiring. Needs a decision on where that
list lives (a `dormant:` section in the existing accounts config versus a
separate file) and on how a promotion gets reviewed rather than applied
automatically.
