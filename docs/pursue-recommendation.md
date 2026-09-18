# Qualification, transferable evidence, and pursue recommendation (Phase 3)

See docs/eligibility.md for the hard-constraint layer this builds on top
of. Together these turn "how well does this job match?" into "should I
actually spend time pursuing this, and why?" — see
`ingestion/evaluation.py::evaluate_opportunity` for the orchestration and
`jobs evaluate <source> <source_job_id>` for the full report.

## Qualification vs. eligibility vs. fit

Three genuinely different questions:

- **Eligibility** (docs/eligibility.md) — am I *allowed* to do this job?
- **Qualification** (`scoring/qualification.py`) — can I *credibly do* this job?
- **Fit** (`docs/scoring.md`, Phase 1/2) — how well does it match, on a
  continuous scale, across role/technical/direction/compensation/etc.?

A candidate can be eligible and unqualified (Case C below), qualified and
ineligible (Case B below), or both eligible and qualified while still not
worth pursuing (Case A below, via opportunity cost).

## Requirement importance

`domain/requirements.py::RequirementImportance` — `hard_required` /
`required` / `preferred` / `informational` / `unknown`. Only
`hard_required` requirements can single-handedly fail qualification;
`preferred` ones never gate anything, full stop (they're excluded from
qualification's averaging entirely, not just down-weighted).

`scoring/requirements.py` detects `hard_required` conservatively: a
skill/years mention must fall within a real "Minimum requirements"-style
section (not merely anywhere in the "required" region) **and** carry an
explicit numeric year threshold. A bare skill mention in that section
without a number is `required`, not `hard_required` — the numeric
threshold is what makes it an unambiguous hard gate.

### A real bug this surfaced: prose vs. heading

Stripe's own posting boilerplate literally says *"...if you meet the
minimum requirements... **The preferred qualifications are a bonus, not a
requirement.** Minimum requirements 6+ years..."* — the phrase "preferred
qualifications" appears once in ordinary prose *before* the real
"Minimum requirements" heading, and once again later as the actual
section heading. A naive "find the first occurrence" heuristic anchored
on the wrong (earlier, prose) occurrence, which caused the entire real
"Minimum requirements" section — including "3+ years of experience as a
Golang software engineer" — to be misclassified as preferred. Fixed by
requiring a marker occurrence to be followed by heading-like content, not
a linking verb ("are", "is", "the", etc.) — see
`scoring/requirements.py::_section_start`'s prose-continuation-word skip.

A second, related bug: the fixed-size (±60 char) context window used to
attach a "5+ years" mention to the nearest skill checked *every* skill in
the taxonomy for a match anywhere in the window, not just the nearest
one — on short/dense text this could attach a hard-section's years
threshold to an unrelated skill mentioned later in the same window. Fixed
by picking only the single nearest skill mention, not every skill that
happens to occur in the window.

Phase 4.1 added `"what you need"`/`"what you'll need"` as required-
section headings (Ashby-hosted postings such as Ramp's use them) and
fixed two more issues the same class of test exposed: a years threshold
inside a required section could attach to a skill mentioned in the prose
*above* the heading (now only skills inside the section are eligible),
and because "what you need" is common in ordinary prose ("what you need
to succeed"), those looser headings don't use the fall-back-to-first-
occurrence rule the original markers do. Measured against all 603
stored jobs, the combined change (with the new `erp_systems` taxonomy
entry) moved exactly five Ramp ERP-specialist consultant roles from
`strong_pursue` to `consider` and changed nothing else.

Both bugs were caught by testing against real job text, not invented —
see `tests/test_requirements_extraction.py::TestRequirementImportance`.

## Qualification statuses

| Status | Meaning |
|---|---|
| `strong` / `moderate` / `weak` | Average match strength across required + hard-required requirements (preferred ones excluded) |
| `fail` | At least one `hard_required` requirement is `unsupported` (zero evidence, direct or adjacent) — dominates regardless of how strong everything else is |
| `unknown` | No resume imported, or no taxonomy-recognized required requirements to judge |

A `hard_required` requirement with only *adjacent* (not direct) evidence
downgrades qualification to `weak` rather than `fail` — the skill is
genuinely evidenced, just not confidently enough for a hard numeric gate.

## Transferable evidence reasoning (optional AI layer)

The deterministic matcher (`scoring/evidence_matcher.py`, Phase 2) is
intentionally literal — it can't tell "no direct evidence of Java or
Python" apart from "no evidence of *any* capability this requirement is
really testing for." `scoring/evidence_reasoner.py` is the first AI layer
introduced specifically to close that gap, for the narrow set of gaps
where it's actually useful.

### AI evidence guardrails — enforced, not just documented

Every guardrail below is enforced by code (see
`tests/test_evidence_reasoner.py` for a direct test of each):

1. **Restricted vocabulary.** `domain/matching.py::AIAssessmentType` has
   exactly four values: `transferable_capability`, `resume_language_gap`,
   `interview_prep_gap`, `no_change`. There is no `direct_evidence` or
   `strong_match` value — the AI is structurally incapable of upgrading a
   gap into a claim of direct experience, because that value doesn't
   exist in the schema it must validate against.
2. **Mandatory, real evidence citation.** `evidence_ids` must be
   non-empty (except for `no_change`) and every id must exist in the
   *candidate's own* evidence index actually shown to the reasoner for
   this job. An assessment citing zero evidence, or evidence that doesn't
   exist, is rejected outright.
3. **Requirement scoping.** `requirement_id` must reference a requirement
   actually offered to the reasoner for this job — an assessment about
   anything else is rejected.
4. **No room to invent.** The schema (`extra: "forbid"`) has no field for
   years of experience, technologies, employers, or degrees — there's no
   slot to put an invented one in, and an attempt to add one is a
   validation failure, not a silently-ignored extra field.
5. **Never gates.** `ai_assessment` is purely additive on
   `RequirementMatch` — it never changes `match_type` or `gap_type`. A
   `hard_required` `fail` from a zero-evidence gap is untouched by
   anything the AI says, because qualification is computed from
   `match_type`/`importance` alone (`scoring/qualification.py`), which the
   AI cannot write to.

### Cost control

Never called for: hard-required gaps (obvious, binary — "do not ask AI to
rescue obvious hard gaps"), or anything already resolved confidently
(`strong_match`/`partial_match`). Only called for `unsupported`/
`adjacent_experience` gaps on non-hard-required requirements — the
genuinely ambiguous cases. The system works fully deterministically with
no API key configured (`scoring/evidence_reasoner.py::get_default_reasoner`
returns `None`), and any reasoner failure (missing package, network
error, malformed output, or a custom reasoner raising) degrades to "skip
AI, keep the deterministic result" — never propagates.

### Provider-neutral by design

`EvidenceReasoner` (ABC) with `ClaudeEvidenceReasoner` as the default
implementation — domain/scoring logic never imports the `anthropic`
package directly, mirroring the existing `scoring/ai.py` pattern from
Phase 1.

## Opportunity cost

Not a life-modeling exercise — `career/opportunity_cost.py` flags the
narrow case where qualification is high but the actual value of spending
time is low: compensation below target *and* career direction weak. High
opportunity cost forces `low_priority` in the pursue recommendation
regardless of how qualified the candidate is (see the real Creative
Circle case below) — a role you'd definitely get the offer for isn't
automatically worth pursuing.

## Scope/ownership classification

`career/scope.py` distinguishes "implement approved wireframes in an
existing template" from "own platform architecture and API design" —
category/phrase presence (reusing the shared skills taxonomy's own
architecture/leadership/customer-facing/AI-ML categories, plus a small
phrase list for execution/ownership/strategy/maintenance, which have no
taxonomy equivalent), never raw keyword counts.

## Freshness

`career/freshness.py`, thresholds configurable in
`career/data/preferences.yaml`'s `freshness_thresholds`
(`fresh_days`/`recent_days`/`aging_days`, default 7/30/90). A `stale`
posting is never treated as closed — the reason text says so explicitly
("still visible at the source; closure not confirmed") — only a direct
job-detail confirmation should ever claim closure, and nothing here
attempts that.

## Pursue recommendation — hard gates dominate

`career/pursue.py::compute_pursue_recommendation`, in strict precedence
order:

1. `eligibility == ineligible` → **do_not_pursue**, regardless of fit.
2. `qualification == fail` (a hard-required gap with zero evidence) →
   **do_not_pursue**, regardless of career-direction or bridge strength.
3. `eligibility in (verify, unknown)` → **verify_first** — a promising job
   must not present as a confident recommendation while something
   necessary to confirm remains open.
4. `opportunity_cost == high` → **low_priority**, even for excellent
   qualification.
5. Otherwise: `strong_pursue` / `pursue` / `consider` / `low_priority`
   from a straightforward weighing of qualification, career direction,
   and immediate opportunity (see `career/pursue.py` for the exact
   branches — deliberately simple, explainable `if`/`elif` logic, not a
   scored formula).

Every recommendation carries `reason` (one sentence) and
`contributing_factors` (the raw inputs that fed the decision) — never a
bare label.

## Work-style fit (Phase 4.2)

A role can be eligible, well qualified, and pointed in the right career
direction while its *day-to-day shape* is still wrong for the candidate.
The motivating case was a remote Ramp "Technical Consultant, Commercial"
posting: APIs, integrations, system design, and technical discovery all
matched, but the job is described as being "on the frontlines" with
customers, acting as a liaison, partnering with Account Management,
representing Product/Engineering externally, and guiding customers
"without writing code directly." The work product is meetings and
communication, not building.

`career/work_style.py::classify_work_style` answers "what does a normal
day in this role consist of?" from the posting's own description. It
never looks at the title or company, so a Solutions Architect posting can
come out build-heavy at one company and customer-heavy at another.

### How evidence is extracted

1. **Role section only.** Text is read from the first role heading
   ("about the role", "what you'll do", "responsibilities", ...) up to the
   first benefits/EEO heading. Company marketing ("everyone is a
   builder") and perks don't count.
2. **Distinct activity phrases, three groups**, each reported verbatim on
   `WorkStyleResult`:
   - *build*: "write production code", "design and ship", "prototype",
     "build integrations", "deploy", "debug", "owning services", "on-call", ...
   - *customer / meeting*: "on the frontlines", "liaison", "discovery
     calls", "demos", "pre-sales", "account management", "executive
     relationships", "externally", ...
   - *coordination / communication*: "coordinate", "facilitate",
     "presentations", "project management", "follow-up communication",
     "manage dependencies", ...
   Phrases describe what the person does. Bare "customer-facing" is
   deliberately excluded because postings use it for the product being
   built.
3. **Explicit no-code statements** ("without writing code directly",
   "non-coding role") are recorded separately and rule out a build-leaning
   classification. The list is narrow on purpose: "... without writing
   code" alone often describes what a product lets its users do.
4. **Weak collaboration phrases** ("cross-functional", "roadmap",
   "influence", "relationship", "go-to-market", ...) appear in plenty of
   hands-on engineering postings, so they count half toward
   customer/coordination weight and can't make a role meeting-heavy on
   their own.

### Classification rules

With `b` build phrases, `people` = strong customer + coordination phrases
+ half of the weak ones:

| Style | Rule |
|---|---|
| `unknown` | fewer than 3 signals in total, description under 200 characters, or no group clearly dominates |
| `build_heavy` | no no-code statement, `b ≥ 3`, and `b ≥ people` |
| `balanced` | no no-code statement, `b ≥ 3`, and `2·b ≥ people` — real customer time alongside real building (a hands-on FDE) |
| `customer_heavy` / `coordination_heavy` | a no-code statement, or at least 2 strong people phrases and `people > b`; the larger strong group decides which |

### Effect on the pursue recommendation

`preferences.yaml` lists disfavored styles (`work_style.disfavored`,
currently `customer_heavy` and `coordination_heavy`). In
`career/pursue.py` the check runs after every hard gate (ineligible,
failed qualification, verify-first) and after high opportunity cost, and
before the normal weighing. A disfavored style caps the result at
`consider`, whatever the technical match, compensation, or title. It is a
downgrade, not a rejection: the role stays visible in `jobs discover` and
`jobs evaluate`, with the evidence listed under WORK STYLE, but it never
reaches the email threshold in either operating mode. `balanced` and
`build_heavy` roles are unaffected; balanced roles that do get emailed
carry a watchout listing the customer-time phrases found.

Measured against the 607 postings stored at the time: of the plain
individual-contributor engineering titles, 94 came out build-heavy, 10
balanced, 95 unknown, and 4 customer-heavy (Anthropic's enterprise
Applied AI Engineer, which is a pre-sales role, and a Stripe integration
liaison role).

### Known limitations

- Phrase lists are approximate English matching, not understanding.
  "Partner with Customer Activation" (an internal team) matches the
  customer phrase "partner with customer".
- About 40% of postings come out `unknown`, usually short or unstructured
  descriptions. `unknown` never changes a recommendation.
- The optional AI layer does not refine work style; the deterministic
  result is the only source.

## Career track (Phase 4.3)

`career/career_track.py`. The Phase 4.2 dry run had 72 alert-ready jobs,
many of them weak: React Native, SAP Commerce, program/account/engineering
managers, an intern, a data scientist. They got there because nothing
asked for *affirmative* evidence that a job is on one of the candidate's
paths: generic API/AWS/AI/integration mentions the candidate can back up
were enough.

Two tracks:

- **Stack-adjacent engineering**: PHP/Laravel/Craft/WordPress, backend,
  full-stack, software/web engineering.
- **Career-direction engineering**: AI/ML engineering (applied AI, LLM,
  GenAI, agents, AI platform), and forward deployed, solutions/customer/
  field/implementation/integration engineering, systems/platform
  engineering, architects.

Classification:

| Result | When |
|---|---|
| `off_track` | The **title** names an unrelated primary identity: mobile, frontend/design systems, embedded/hardware/semiconductor/controls, enterprise-product specialisms (SAP, Appian, Informatica, MuleSoft, ServiceNow, …), data science/research, security specialism, management (manager/director/head of), sales/pre-sales/account roles, junior/intern/associate levels ("Senior Associate" excepted). |
| `aligned` | An on-track title **and** matched experience in the description. AI titles need a supported AI requirement plus a supported systems requirement. Delivery titles (FDE, SA, SE, …) need two supported systems requirements. Stack titles need stack overlap plus a systems requirement, or two systems requirements. |
| `unclear` | A neutral title ("Industry Principal"), or an on-track title without that evidence. |

Only the title can make a job off-track. A technology in the title
defines the role; the same word in the description is usually incidental
(an FDE posting that mentions React is not a React role). Supported means
a strong or partial match on a non-preferred requirement.

Effect (applied after every other rule, only to `pursue`/`strong_pursue`):
`off_track` caps at `consider`; `unclear` caps `strong_pursue` at
`pursue`; `aligned` changes nothing. So `strong_pursue` now always has an
explanation of why the job fits this candidate. The result is stored in
`job_evaluations.career_track` and shown by `jobs evaluate` (CAREER TRACK)
and `jobs discover`.

On the Phase 4.2 dry-run set, together with the keyword-boundary fix
(docs/scoring.md) and the eligibility additions below, this took
`strong_pursue` from 73 to 44, removing every false positive listed
above.

Eligibility additions found in the same review: security-clearance
phrasings used by government contractors ("Clearance Required" in a
title, "Secret clearance", "TS/SCI", "CAC eligibility", "DHS public trust";
bare "public trust" is ignored because it appears in ordinary prose), and
Korea/Taiwan/Vietnam as non-US remote scopes.

### Specialist platforms (Phase 4.4)

A generic title can still hide a platform specialism: GitLab's "Staff
Engineer, People Technology" reached `strong_pursue` with real API,
automation, and PHP overlap while actually being Workday (8 mentions) and
Workato (4) depth. So a short list of specialist platforms (Workday,
Workato, SuccessFactors, UKG, BambooHR, SAP, NetSuite, Oracle ERP,
Dynamics 365, ServiceNow, Appian, Pega, Informatica, MuleSoft, Boomi,
Sitecore, AEM) is counted in the title+description: **three or more
mentions means the platform defines the job**, and the result is
`unclear` (capping `strong_pursue` at `pursue`) unless the candidate has
evidence for it. Repetition is what separates defining from incidental —
one SAP mention in an integration role is context, eight Workday mentions
are the job. It stays `unclear`, not `off_track`, because a
description-level signal is weaker than a title.

Known limitations: generic engineering titles whose descriptions overlap
the candidate's experience without a specialist platform ("Lead Software
Engineer, Ads") still pass, and a vendor-specific FDE role (Kong) is
still `aligned`. Both are what the bounded AI review
(docs/scoring.md#bounded-ai-refinement) is for.

## Regression cases (real jobs, live-discovered)

All three verified end-to-end through the actual CLI
(`tests/test_regression_real_cases.py` covers them offline against a
synthetic evidence index; the live-data versions were run through
`jobs evaluate` directly):

| Case | Job | Qualification | Eligibility | Pursue |
|---|---|---|---|---|
| A | Creative Circle "Senior Web Developer — PHP" | `strong` | `eligible` | **`low_priority`** — strong qualification, but $70–75/hr is below the contract floor and career direction is weak; opportunity cost is `high` |
| B | Stripe "Technical Solutions Engineer" | `moderate` | `verify` | **`verify_first`** — the timezone requirement (America/Chicago vs. "Mountain or Pacific") is ambiguous and must be resolved before a confident recommendation |
| C | Stripe "Backend Engineer... Platform" | `fail` | `eligible` | **`do_not_pursue`** — hard-required "3+ years professional Golang" has zero evidence; PHP is only a *preferred* qualification and cannot rescue it |

Case A's own strong PHP evidence, Case B's genuinely strong bridge
signal, and Case C's otherwise-solid PHP/REST-API match all fail to
produce `strong_pursue` — which is the entire point of separating
eligibility, qualification, and opportunity cost from raw fit.

## Evaluation versioning and persistence

`domain/opportunity_decision.py::EVALUATION_VERSION` (currently
`"opportunity-decision-v3"`, bumped in Phase 4.2 when work style was added and in Phase 4.3 for career track) is stamped on every stored
`JobEvaluationRecord` (`storage/db.py`, append-only like
`JobScoreRecord`) — `jobs evaluate` persists a new row each time it's
run, so a job's recommendation history is inspectable over time, and it's
possible to tell whether a later value changed because the job's data
changed, the evidence changed, preferences changed, or the evaluation
logic itself changed.

**A real staleness gap this exposed**: `jobs evaluate` reads the *latest
stored* `CareerFitResult` (`storage/repository.py::latest_fit_from_record`)
rather than recomputing fresh. During this phase's own development, a
requirement-extraction bugfix (see above) was made *after* some jobs had
already been scored — `jobs evaluate` briefly showed a stale, pre-fix
`experience_detail` for one of the regression cases until the job was
re-searched. Nothing currently detects this automatically: `SCORER_VERSION`
(Phase 1) didn't change even though the underlying requirement-extraction
behavior did. This is a known, documented gap (not silently ignored) —
see docs/architecture.md's Phase 4 recommendations for
version-based staleness detection as a candidate fix, and for now the
practical mitigation is the same as always: re-run a search/discover pass
to refresh a job's stored score before trusting `jobs evaluate` on it.
