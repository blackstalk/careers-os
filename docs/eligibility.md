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

**Production correction (Phase 4.1)**: `work_preferences.hybrid`/`onsite`
were originally `acceptable`, which meant a hybrid or onsite posting
cleared the work-arrangement check as `ELIGIBLE` — nothing then stopped a
strong technical/career-direction score from reaching `strong_pursue`
regardless of work arrangement. Both are now `unacceptable`, reflecting
the candidate's actual current search (remote-only): a hybrid/onsite
posting is now `INELIGIBLE`, which `career/pursue.py` already treats as
`do_not_pursue` ahead of any fit score — the same hard-gate machinery
every other eligibility constraint already used. This was a config
change, not a code change.

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
are handled (`domain/eligibility.py::ConstraintType`), one of which
(`geographic_residency`) has two independent detection paths:

- **Timezone** (`"located"/"based"/"reside" ... Pacific/Mountain/Central/Eastern ... time zone`) — always resolves to `verify`, never `ineligible`, even when the candidate's timezone doesn't match the named region. This is deliberate: "located in the Pacific time zone" is genuinely ambiguous between physical residency and working-hours overlap (see the real Stripe Technical Solutions Engineer case below), and guessing either way would be worse than asking.
- **Unambiguous residency (from job text)** (`"must reside/live in <place>"`, no timezone framing) — this phrasing has no such ambiguity, so it resolves directly to `eligible`/`ineligible` by comparing the named place against `candidate.location`.
- **Remote role scoped to a country (from the job's structured `location` field, Phase 4.1)** — a genuinely `remote_status=remote` job can still be scoped to one country via the source's own `location` field rather than description prose (e.g. Ashby/Lever set `location` to `"India - Remote"` or `"Remote (Germany)"`) — `_check_residency` only ever scans title+description text, so this would otherwise pass silently as eligible. `_check_remote_location_geography` catches the common `"<non-US country/region> ... remote"` pattern and resolves to `verify` (not `ineligible` — a location string naming a country is a real but ambiguous signal, same posture as the timezone check). Deliberately a short, explicit, non-exhaustive country/region list, not a general geography parser.
- **Work authorization / sponsorship** (`"authorized to work ... without sponsorship"`, `"will not sponsor"`, etc.) — compared against `work_authorization.authorized`/`sponsorship_required`.
- **Security clearance** (`"security clearance"`, `"active clearance"`) — compared against `security_clearance.held`; `null` → `unknown`, not a guess.
- **Relocation** (`"relocation required"`, `"must relocate"`) — compared against `relocation.willing`.
- **Work arrangement** — not text-based; compares the job's already-normalized `remote_status` (`onsite`/`hybrid` compared directly; `unknown` handled as described in "A note on `unknown`" below) against `work_preferences`.

### A note on `unknown` work arrangement

`remote_status=unknown` means the source didn't publish enough to
classify the arrangement. It is never inferred to be remote, and never
treated as a rejection:

- If the candidate accepts hybrid or onsite work, `unknown` triggers no
  work-arrangement check at all (the standing "never penalize a field the
  source didn't publish" rule).
- If the candidate is **remote-only** (`hybrid` and `onsite` both
  `unacceptable`, as `candidate.yaml` is today), `unknown` produces a
  `verify` check — "work arrangement not stated, confirm before
  pursuing." The pursue layer turns that into `verify_first`, so the role
  still appears in `jobs discover` but is never emailed.

This was added after the first full Phase 4.1 dry run: of 38
`strong_pursue` opportunities, 15 were explicitly remote and 23 had no
stated arrangement — and all 5 selected email slots went to the
unstated ones (mostly OpenAI Ashby postings with `location="San
Francisco"` and no `workplaceType`). For a remote-only search, "not
stated" is an open question, not a remote-compatible answer.

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
