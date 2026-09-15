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
