# Eligibility and hard requirements (Phase 3)

This is the first of two documents describing Phase 3's decision layer —
see docs/pursue-recommendation.md for qualification, transferable
evidence, AI guardrails, opportunity cost, scope, freshness, and the
final pursue recommendation.

## Why eligibility is separate from fit

A job can be an excellent fit and still be something you literally cannot
apply to (a timezone-residency requirement, a clearance you don't hold, a
relocation you won't make). Conflating that with `overall_fit` would mean
a 91%-fit job with a hard geographic block looks the same as a 91%-fit
job with no blockers at all. `career/eligibility.py::evaluate_eligibility`
is entirely separate from `scoring/`, runs first in the decision chain
(`ingestion/evaluation.py`), and its result can override everything else
in the final pursue recommendation (see docs/pursue-recommendation.md).

## Candidate facts (`career/data/candidate.yaml`)

A new, narrowly-scoped config file — facts about the candidate, not
preferences about scoring (`career/data/preferences.yaml`) and not
positioning (`career/data/profile.yaml`):

```yaml
location: {city, state, country, timezone}
work_authorization: {country, authorized, sponsorship_required}
work_preferences: {remote, hybrid, onsite}   # preferred | acceptable | unacceptable
relocation: {willing}
security_clearance: {held}                    # true | false | null
```

`null`/omitted means genuinely unknown — eligibility checks never guess a
missing fact, they report `unknown` (see below).

## Statuses

| Status | Meaning |
|---|---|
| `eligible` | No hard constraint detected, or a detected one is satisfied |
| `ineligible` | A detected constraint is unambiguously not satisfied |
| `verify` | The job's own wording is ambiguous — could be read more than one way |
| `unknown` | The wording is clear, but the candidate fact needed to judge it isn't configured |

A job with no constraints detected at all rolls up to `eligible` — most
postings don't state any. When multiple checks fire, `ineligible` beats
`verify` beats `unknown` beats `eligible` (`domain/eligibility.py::rollup_status`).

## What's detected, and how conservatively

Every check requires one of a small set of well-understood phrasings —
nothing here infers a constraint from a vague hint. Six constraint types
are handled (`domain/eligibility.py::ConstraintType`):

- **Timezone** (`"located"/"based"/"reside" ... Pacific/Mountain/Central/Eastern ... time zone`) — always resolves to `verify`, never `ineligible`, even when the candidate's timezone doesn't match the named region. This is deliberate: "located in the Pacific time zone" is genuinely ambiguous between physical residency and working-hours overlap (see the real Stripe Technical Solutions Engineer case below), and guessing either way would be worse than asking.
- **Unambiguous residency** (`"must reside/live in <place>"`, no timezone framing) — this phrasing has no such ambiguity, so it resolves directly to `eligible`/`ineligible` by comparing the named place against `candidate.location`.
- **Work authorization / sponsorship** (`"authorized to work ... without sponsorship"`, `"will not sponsor"`, etc.) — compared against `work_authorization.authorized`/`sponsorship_required`.
- **Security clearance** (`"security clearance"`, `"active clearance"`) — compared against `security_clearance.held`; `null` → `unknown`, not a guess.
- **Relocation** (`"relocation required"`, `"must relocate"`) — compared against `relocation.willing`.
- **Work arrangement** — not text-based; compares the job's already-normalized `remote_status` (only when it's definitively `onsite` or `hybrid`, never `unknown`) against `work_preferences`.

Certifications, mandatory degrees, and required licenses are **not**
implemented as hard-constraint checks in Phase 3 — real postings almost
always hedge degree requirements with "or equivalent experience" (verified
directly in the Stripe TSE posting text), and a false `ineligible` there
would be worse than not checking at all. This is a known, documented
scope limit, not an oversight — see docs/architecture.md's Phase 4
recommendations.

## Worked example: the real Stripe timezone case

Stripe's Technical Solutions Engineer posting says: *"This role is remote
but requires candidates to be located in the Mountain or Pacific time
zones."* The candidate is configured as `America/Chicago` (Central).

```
Requirement: located in the Mountain or Pacific time zones
Candidate: America/Chicago
Status: VERIFY
Reason: Time-zone phrasing is ambiguous between physical residency and
        working-hours overlap — verify with the employer rather than
        assuming either interpretation.
```

This is a real, live-discovered case (not a synthetic example) — see
docs/pursue-recommendation.md's regression-cases section for how it
carries through to the final `verify_first` recommendation.

## Two regex bugs this surfaced (documented, not hidden)

Building this against real text caught two real bugs, both fixed and
covered by tests:

1. `re.IGNORECASE` on the residency regex also flattened the capture
   group's case-sensitivity, so `[A-Z]`-based word-boundary detection
   silently broke — "Must reside in Texas for this position." captured
   "Texas for this position." as the place name instead of "Texas".
   Fixed with a scoped inline flag (`(?i:...)`) around only the literal
   phrase, keeping the capture group case-sensitive.
2. (Documented in docs/pursue-recommendation.md, not here, since it's a
   qualification/hard-requirement bug, not an eligibility one.)
