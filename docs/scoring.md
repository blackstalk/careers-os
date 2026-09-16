# Career-fit scoring

## Goal

Explain *why* a job scored the way it did, not just produce one opaque
number. Every score is a `FitComponent`:

```json
{
  "score": 0.88,
  "reason": "The role combines technical architecture, implementation, and stakeholder-facing delivery.",
  "evidence": ["matched: solutions architect", "matched: forward deployed engineer"],
  "confidence": 0.8
}
```

`confidence` is honesty about the input data, not a second score. A
component built from a two-sentence teaser description should report low
confidence; one built from a full job description should report higher
confidence. A missing field (no salary published, no work-arrangement
stated) must be represented as *unknown with low confidence*, never
guessed at or silently treated as a rejection.

## Components

| Component | What it measures | Phase 1 implementation |
|---|---|---|
| `role_fit` | Does the work resemble target roles (FDE, Solutions Architect, AI Engineer, ...)? | keyword match against `career/data/profile.yaml`'s `target_role_keywords`, title match weighted higher than body-only match |
| `technical_fit` | Architecture/APIs/distributed systems/AI/cloud/automation content | keyword match against `technical_interest_keywords` |
| `career_direction_fit` | Does this move toward the target career direction, credibly for this candidate? | evidence overlap in forward-direction categories when available (Phase 4.1); keyword match against `career_direction_keywords` otherwise — see below |
| `compensation_fit` | Comparison against configured target comp | deterministic range comparison — see below |
| `work_arrangement_fit` | Remote preference | deterministic mapping from `remote_status` |
| `experience_fit` | Requirement-vs-evidence match | **real as of Phase 2** — deterministic matching against imported resume evidence (see docs/evidence-model.md). Falls back to the Phase 1 neutral placeholder (0.5, near-zero confidence) only when no resume has been imported yet |

`overall_fit` is a weighted average of the six components, using weights
from `career/data/preferences.yaml` (`component_weights`) — configuration,
not a code constant. It's provided for sorting/ranking convenience; the
per-component breakdown is the thing meant to actually be read.

## Deterministic first (`scoring/deterministic.py`)

Every component above is computed with plain rules — string matching and
range comparisons — with **no LLM call required**. This is the whole
reason `jobs search creative-circle` produces scored, explained results
out of the box with no API key configured.

### Compensation fit

- Full-time roles are compared against `preferences.yaml`'s
  `compensation.full_time` (minimum/strong annual figures); contract roles
  against `compensation.contract` (minimum/strong/exceptional hourly).
- **Salary figures are never compared against hourly thresholds or vice
  versa** — this mirrors the source-layer rule of never cross-converting
  compensation units (see `docs/sources/creative-circle.md`).
- **Missing compensation data scores neutral (0.5) at very low confidence
  (<0.3), never zero.** Per the product requirement, an interesting
  strategic role must not be auto-rejected just because pay wasn't
  published.

### Work arrangement fit

Remote scores highest, hybrid is lightly penalized
(`work_arrangement.hybrid_penalty`, default 0.1), onsite is penalized more
(`work_arrangement.onsite_penalty`, default 0.35) — configurable, and never
zero. This is a soft scoring signal only. Whether hybrid/onsite is
allowed at all is a hard eligibility fact in `career/data/candidate.yaml`
(currently remote-only — see docs/eligibility.md), checked before any
score matters.

### Role / technical / career-direction fit

`role_fit`/`technical_fit` are honestly weak, by design: keyword overlap
against `career/data/profile.yaml` is a deterministic *first pass*, not a
claim that it reliably judges whether a role is "truly architectural vs.
merely titled architect." Confidence is capped lower when the available
description is short (e.g. a search-result teaser rather than a full job
posting), which is the honest signal that the keyword match had little
text to work with.

#### `career_direction_fit`: evidence-grounded when possible (Phase 4.1)

This component used to be the same pure job-text keyword heuristic as
`role_fit`/`technical_fit` — which meant it saturated identically (score
1.0) for *any* posting mentioning 2+ target-direction phrases, regardless
of whether the candidate had any real evidence for that direction. In
production, five real Applied AI Engineer/Forward Deployed postings at
different companies all scored `career_direction_fit=1.0`,
`role_fit=1.0`, `technical_fit=1.0` — identically — proving the score
carried zero discriminating information once a posting was "in the
target space" at all: title/keyword resemblance, not evidence.

`scoring/deterministic.py::score_career_direction_fit` now checks
`ExperienceFitDetail.requirement_matches` (when available) for real
matches in a forward-direction category — the same categories
`career/bridge_role.py::BRIDGE_SIGNAL_CATEGORIES` already uses
(architecture, cloud, ai_ml, leadership, customer_facing) — and derives
the score directly from those match strengths (reusing the same
strong/partial/adjacent/unsupported → score mapping
`scoring/qualification.py` uses, not a new scale). A target-direction
title with unsupported evidence in every forward category now scores
low; a plain PHP/Laravel posting that happens to carry real
architecture-category evidence scores on that evidence, with no
FDE/AI terminology required at all (see the two-path framing in
docs/discovery.md). Falls back to the original keyword-only heuristic,
at its original (lower) confidence, only when no resume was imported or
this specific posting extracted no forward-category requirements at
all — never worse than the prior behavior in that case.

**Interaction with the optional AI layer.** `scoring/ai.py` asks an LLM
whether a job's responsibilities genuinely match the target direction
(skeptical of title inflation). It never sees candidate evidence, so when
`career_direction_fit` is evidence-grounded the AI score can only
*lower* it, never raise it — otherwise production (which runs with an
API key) would quietly reintroduce the title-resemblance problem this
change exists to remove. On the keyword fallback path the AI score
replaces the keyword score as before.

**Taxonomy granularity (Phase 4.1).** The shared skills taxonomy used to
have a single `ai_ml` skill, so "5+ years training large-scale models in
PyTorch" and "shipped LLM-backed features" were the same requirement —
application-level AI evidence satisfied model-training requirements
directly. `ml_model_training` (adjacent to `ai_ml`) and `gpu_programming`
(no adjacent skills) now split that out, so a research/training-heavy
Applied AI role produces a real hard-requirement gap instead of a strong
match. These are new taxonomy keys, so they don't retroactively tag
existing evidence; re-import the resume if it ever gains real training
or GPU experience.

Company/provider identity plays no role in either path — nothing in this
function's inputs (job text, requirement matches) ever carries company
or source identity.

### Experience fit

Unlike the four components above, `experience_fit` is not keyword overlap
against a static profile — it's a real comparison between the job's
extracted requirements (`scoring/requirements.py`) and imported career
evidence (`scoring/evidence_matcher.py`), classified as
strong/partial/adjacent/unsupported/unknown per requirement, with a
matching skill-gap taxonomy. This is substantial enough to have its own
document: see **docs/evidence-model.md**. The summary reported here is
just the concise `FitComponent` view; the full per-requirement breakdown
lives on `CareerFitResult.experience_detail` and is what `jobs match` /
`jobs evidence` / `jobs gaps` / `jobs recommend-resume` read.

If no resume has been imported yet, `experience_fit` degrades to the
honest Phase 1 placeholder (neutral score, near-zero confidence) — see
`scoring/deterministic.py::score_experience_fit`.

## Optional AI layer (`scoring/ai.py`)

If `ANTHROPIC_API_KEY` is set and the `anthropic` package is installed,
`scoring/engine.py` additionally asks an LLM to judge `role_fit` and
`career_direction_fit` against the full job description — specifically
targeting the kind of judgment keyword matching can't do (title inflation,
"is this actually FDE-shaped work"). Its output *replaces* those two
components' score/reason/evidence and raises `ai_evaluation_included` to
`true` on the result; the other four components are always deterministic
regardless.

Every failure mode here — no key, package not installed, network error,
malformed model output — degrades to "skip AI evaluation" and falls back
to the pure deterministic result. `scoring/engine.py:score_job` runs
correctly with `use_ai=False` (or with no key configured) and this is
covered directly by
`tests/test_scoring_deterministic.py::TestScoreJobEngine::test_runs_without_ai_when_no_api_key`.

```
Deterministic Scoring (always runs, no API key required)
        +
Optional AI Semantic Evaluation (role_fit, career_direction_fit only)
        ↓
Combined CareerFitResult (ai_evaluation_included flag records which happened)
```

## Configuration, not code

`career/data/profile.yaml` (who I am / what I'm targeting) and
`career/data/preferences.yaml` (comp targets, work-arrangement penalties,
component weights) are the only places meant to change when tuning
scoring. No personal preference is hard-coded into
`sources/creative_circle/` — the source adapter has no idea what a "good"
job looks like, by design (see docs/architecture.md's central principle).
