# Discovery (Phase 2.5)

Phase 1 and 2 built the infrastructure (multi-source ingestion, evidence-
based scoring). Phase 2.5 is about *using* it: searching broadly on
technologies with strong existing market credibility, then letting Career
OS rank the results against both immediate fit and long-term career
direction — without requiring four separate manual searches.

## Search profiles

`career/data/search_profiles.yaml` — editable, no code change needed to
add a profile:

```yaml
profiles:
  php:
    enabled: true
    queries: [PHP]
  laravel:
    enabled: true
    queries: [Laravel]
  craft:
    enabled: true
    queries: ["Craft CMS"]
  wordpress:
    enabled: true
    queries: [WordPress]
  backend_platform:
    enabled: true
    queries: ["Backend Engineer", "Platform Engineer", "API Integration"]
  fde:
    enabled: true
    queries: ["Forward Deployed", "Solutions Architect", "Applied AI", ...]

default_employment_types: [full_time, contract, freelance, part_time, unknown]
```

Each profile is one or more independent keyword queries — a job matching
**any one** query from **any one** profile is discovered; nothing requires
matching all of them. `career/search_profiles.py::SearchProfilesConfig`
loads this; `jobs discover --profile <name>` (repeatable) runs a specific
subset, overriding a profile's own `enabled: false` if named explicitly.

### Two transition paths (Phase 4.1)

Discovery deliberately covers two different kinds of good opportunity,
and neither is required to look like the other:

- **Path A — stack-adjacent.** Roles where the existing stack (PHP,
  Laravel, Craft CMS, WordPress, backend/API work, AWS) is the bridge,
  ideally with architecture/systems/AI/integration responsibility on top.
  Found by the `php`/`laravel`/`craft`/`wordpress` profiles and, since
  Phase 4.1, `backend_platform` — which catches backend/platform/
  integration roles that never name those technologies.
- **Path B — target direction.** Forward Deployed Engineer, Solutions
  Architect, Applied AI, ML Systems, Customer Engineer roles, found by
  `fde`. These don't need to mention PHP at all.

Neither path is a scoring rule by itself — both are just search
coverage. What decides whether a discovered role is worth an email is the
same decision pipeline for both: remote compatibility (eligibility),
qualification against real evidence, and `career_direction_fit`, which
since Phase 4.1 is grounded in matched candidate evidence rather than
title keywords (docs/scoring.md#career-direction-fit). A Path B title with
no real evidence overlap scores low; a Path A role with real architecture
evidence scores well without any FDE/AI wording.

### Employment-type policy

The initial version of this file only listed `contract`, `freelance`,
`part_time` — matching the stated goal of testing the market without
giving up a current full-time role. A live discovery run immediately
exposed a problem: Greenhouse almost never exposes `employment_type` at
all (see docs/sources/greenhouse.md), so nearly every Greenhouse posting
normalizes to `unknown` — and a strict allowlist silently hid a genuinely
interesting 56%-fit Stripe match for that reason alone. Excluding a role
because the *source* didn't publish a field is exactly the thing this
project has refused to do everywhere else (missing compensation, missing
remote status, etc. — see docs/scoring.md). `unknown` was added to the
default for consistency; `full_time` remained excluded at that point,
since it was a real, known, deliberate signal, not a gap.

Phase 4's Ashby/Lever sources (docs/sources/ashby.md,
docs/sources/lever.md) target Forward Deployed Engineer/Solutions
Architect/Applied AI/ML Systems roles, which are almost entirely
`full_time` — so `full_time` was added to the default too. The
reasoning changed along with it: "full_time" was originally being used
as a rough proxy for "not interesting right now," but the project has
since built an entire decision-intelligence layer
(docs/pursue-recommendation.md) specifically to answer "is this
opportunity worth my attention" with actual evidence — eligibility,
qualification, transferable fit, career direction, opportunity value —
rather than a blunt field-value filter. A sufficiently compelling
full-time role (including one using PHP/Laravel/Craft skills already on
the resume, discovered via the *existing* profiles) is exactly the kind
of opportunity worth surfacing as a potential replacement for the
current role, not something to hide before it's ever evaluated.
`full_time` gets no scoring advantage for being full-time — it simply
reaches the same pipeline, and the same passive-mode `strong_pursue`
alert threshold (docs/alerts.md), as everything else.

## The discovery command

```bash
jobs discover
jobs discover --profile laravel --profile craft --remote --limit 10
jobs discover --source creative-circle --min-fit 0.5
jobs discover --employment-type full_time --employment-type contract
```

For every enabled profile's every query, against every configured source,
`ingestion/discovery.py::run_discovery` reuses the existing Phase 1/2
pipeline (`run_search_ingestion`) unchanged — discovery adds ranking and
cross-profile provenance on top, it does not reimplement search,
normalization, deduplication, or scoring. Options:

| Flag | Effect |
|---|---|
| `--profile` (repeatable) | Limit to specific profile(s); default all enabled |
| `--source` | `creative-circle`, `greenhouse`, `ashby`, `lever`, or `workable`; default all |
| `--remote` | Remote-only |
| `--employment-type` (repeatable) | Override the profile config's default allowlist |
| `--min-fit` | Only show `overall_fit >=` this |
| `--limit` | Max opportunities *displayed* (metrics still reflect the full unique set) |
| `--ai/--no-ai` | As in `jobs search` |

### Source behavior respected, not assumed

- **Creative Circle**: one query per profile, keyword passed straight to
  the source's own server-side search (see docs/sources/creative-circle.md).
- **Greenhouse**: one `GreenhouseSource` per board configured in
  `sources/greenhouse/boards.yaml`, queried once per profile — there is no
  cross-company search, and filtering happens client-side inside the
  adapter exactly as documented in docs/sources/greenhouse.md. Board list
  was left unchanged for Phase 2.5 (anthropic, stripe, figma, brex) — see
  the live-validation findings in the Phase 2.5 report for why none of
  them turned out to have PHP/Laravel/Craft/WordPress work, and why no
  boards were added speculatively to chase that.

## Discovery provenance

`JobRecord.discovered_by_profiles` (a JSON list column,
`storage/repository.py::record_search_profile_match`) accumulates which
profile(s) turned up a job — a job found by both `php` and `laravel`
queries gets both names on one row, never a duplicate job. This is purely
additive to Phase 2's schema; a small in-process migration
(`storage/db.py::_apply_lightweight_migrations`) adds the column to an
already-existing local database rather than requiring a fresh one.

## Bridge roles

A "bridge role" (`career/bridge_role.py`) uses a discovery-anchor
technology (PHP/Laravel/Craft CMS/WordPress — anything tagged
`programming_language` or `framework` in the shared skills taxonomy) while
also showing signal toward the target direction (architecture, cloud,
AI/ML, leadership, or customer-facing work — the taxonomy's own
categories, see docs/evidence-model.md).

Classification is **category presence, not keyword frequency** — "avoid
keyword dominance" is enforced directly: a role mentioning "PHP" ten times
with nothing else present is `weak`; a role mentioning PHP once alongside
architecture, AWS, and technical leadership is `strong`. Distinct
categories present, not total word count, decide the tier:

| Distinct bridge categories found | Classification |
|---|---|
| ≥3 | `strong` |
| 1-2 | `moderate` |
| 0 (anchor present, nothing else) | `weak` |
| no anchor tech present at all | `none` |
| <40 chars of description available | `unknown` |

Commodity-work phrases (content updates, theme modifications, landing-page
production, plugin configuration, etc.) are detected and surfaced in the
`weak` reason text as corroborating signal — **never as an exclusion
rule**. A `weak`-bridge, high-comp WordPress contract still appears in
results; it just doesn't rank as if it were moving your career forward.

## Immediate opportunity vs. career direction

Two separate, deliberately independent assessments
(`career/opportunity_value.py`), both Strong/Moderate/Weak/Unknown:

- **Immediate opportunity** — "can I credibly win this today?" Built from
  `experience_fit`, `technical_fit`, `compensation_fit`, and
  `work_arrangement_fit` only.
- **Career direction** — "does this move me where I want to go?" Built
  from `career_direction_fit` and `role_fit` only.

They can diverge freely: a role can be a strong immediate opportunity and
a weak career-direction move (a well-paid, purely maintenance PHP
contract) or the reverse (a lower-paying but architecturally rich role at
a company you'd take a pay cut to join). Neither classification excludes a
job from results — both are visible, and both feed ranking.

`Unknown` is a distinct outcome from `Weak` in both assessments — reserved
for genuinely insufficient signal (e.g. every underlying component has
near-zero confidence), never used as a soft way to say "bad."

## Ranking philosophy

**Not a sort by `overall_fit`.** `career/discovery_ranking.py::compute_rank_score`
combines four independently-visible dimensions using configurable weights
(`career/data/preferences.yaml`'s `discovery_ranking_weights`):

```yaml
discovery_ranking_weights:
  overall_fit: 0.40
  bridge_role: 0.20
  immediate_opportunity: 0.25
  career_direction: 0.15
```

Bridge/opportunity/direction classifications are mapped to numeric weights
for ranking purposes only (strong=1.0, moderate=0.6, weak=0.25-0.35,
none=0.0, **unknown=0.4-0.5 — treated as neutral, not penalized like a
known-weak or known-none result**). Every dimension that feeds the score
is printed alongside the job in `jobs discover` output, so the ranking is
always traceable back to what actually drove it — never an opaque number.

This is genuinely load-bearing: in the Phase 2.5 live validation run, two
Stripe roles with `overall_fit` of 56% and 36% ranked above nothing lower
in the result set (the Creative Circle PHP contract, at a raw `overall_fit`
of 27%, would have looked comparable on `overall_fit` alone but ranked
lower once bridge/direction were factored in) — see the Phase 2.5 report
for the full breakdown.

### Staleness (Phase 4.4)

A posting still listed months later competes for the same alert budget as
a fresh one — three aged 91-153 days sat in one run's top 15.
`career/discovery_ranking.py::apply_freshness_penalty` multiplies a
`stale` posting's rank score by 0.85. A penalty, not a filter: staleness
is never confirmed closure, and the alert already carries a "Posting is
stale" watchout.

## Phase 3 update: pursue-worthiness leads the output

As of Phase 3, each `jobs discover` result also carries a full
`OpportunityDecision` (eligibility, qualification, opportunity cost,
scope, freshness, and a final pursue recommendation) computed via
`ingestion/evaluation.py::evaluate_opportunity` — the same reasoning chain
`jobs evaluate` prints in full. The result listing leads with
`Pursue: <RECOMMENDATION>`, and the run summary includes a `Pursue
breakdown` line (counts per recommendation across every unique job
discovered, not just the displayed slice). See
docs/eligibility.md and docs/pursue-recommendation.md for what drives
that recommendation — bridge role and immediate/direction assessments
(Phase 2.5) remain visible per-opportunity but no longer solely determine
ranking priority the way they did before hard eligibility/qualification
gates existed.

## What Phase 2.5 deliberately did not add

Per the phase's own scope boundary: no scheduled ingestion, no background
workers, no notifications, no company-intelligence enrichment, no
dashboard, no application CRM expansion, no portfolio/GitHub evidence
ingestion, no AI evidence reasoning, no automatic resume rewriting, and no
automated applications. Those remain Phase 3+ candidates.
