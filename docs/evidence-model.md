# Evidence model

This is what turns "job ingestion + generic fit scoring" (Phase 1) into
"personal career intelligence" (Phase 2): **jobs are matched against
evidence, not just keywords.**

## Provenance is not optional

Every `Evidence` item (`domain/evidence.py`) carries an `EvidenceProvenance`
— where it came from (a resume variant, eventually a portfolio case study
or GitHub repo), which section, which company/role if applicable. Nothing
is manufactured: every evidence item traces back to text an importer
actually read (`career/resume_import.py`). There is no code path that
invents a technology or a responsibility because it "would commonly
accompany" something else — that's explicitly disallowed, including for
the optional AI layer (see "AI guardrails" below).

One concrete consequence: PHP appears explicitly in bullets at HedgeHog
(2004–2012) and Fifth Ring (2019–2020), but Riviana's bullets describe
"Laravel" and "Craft CMS" (PHP frameworks) without ever saying the word
"PHP". The system does **not** infer "uses Laravel → therefore PHP" —
`career/skills.py`'s taxonomy matching is literal alias matching, and
`years_for_skill("php", ...)` will honestly under-count real PHP
experience as a result (see `careers experience` output — it reports
"6-9 years" for PHP, which is a real floor, not the true total). This is a
deliberate tradeoff: honesty over completeness. It's a good candidate for
optional AI-assisted extraction later, but even then the AI guardrails
below would require it to point at the literal evidence text it's drawing
the inference from, not just assert the connection.

## The shared taxonomy

`career/data/skills_taxonomy.yaml` is the single vocabulary shared by:

- **Requirement extraction** (`scoring/requirements.py`) — turns a job's
  title+description into a list of `JobRequirement`s by matching taxonomy
  aliases.
- **Evidence tagging** (`career/resume_import.py`) — the same taxonomy
  tags each bullet/skills-table row with the canonical skill keys it
  mentions (`Evidence.canonical_skills`).

Using one taxonomy on both sides is what makes matching a direct
comparison instead of two independent classifiers that might drift apart.
Each entry also declares `adjacent` skills, used only for
partial/adjacent-match classification (never for strong_match) — see
below.

## Match classification

For every job requirement with a taxonomy mapping, `scoring/evidence_matcher.py::match_requirement`
produces exactly one of:

| Match type | Meaning | How it's decided |
|---|---|---|
| `strong_match` | The skill itself is directly evidenced | The canonical skill appears in ≥1 `Evidence.canonical_skills` |
| `partial_match` | Substance likely present under different framing | No direct evidence, but ≥2 of the skill's configured `adjacent` skills are evidenced |
| `adjacent_experience` | Thin, plausible transferable experience | No direct evidence, exactly 1 adjacent skill evidenced |
| `unsupported` | No real experience gap masked as anything else | No direct or adjacent evidence at all |
| `unknown` | Can't be judged deterministically | The requirement had no taxonomy mapping in the first place (see `scoring/requirements.py` — free-form responsibility prose is not NLP-parsed in Phase 2) |

A years-gated requirement (e.g. "5+ years of AWS") that has direct
evidence but insufficient computed duration is downgraded from
`strong_match` to `partial_match` — the skill is real, the tenure claim
just isn't fully supported yet — rather than treated as unsupported.

## Gap classification is a heuristic, not a verdict

`domain/matching.py::MATCH_TYPE_TO_GAP_TYPE` maps each non-strong match
type onto one gap label:

```
partial_match        -> resume_language_gap   (substance likely present, differently framed)
adjacent_experience   -> interview_prep_gap     (real but thin — a refresher would help)
unsupported           -> real_experience_gap    (no evidence at all, direct or adjacent)
unknown               -> unknown                (not enough structure to judge)
```

This is a **starting heuristic**, stated plainly rather than dressed up as
a semantic classifier. It's a direct, explainable function of the
match-type decision above — not a separate model, and not something an AI
call is allowed to override (see below). Treat it as a first cut worth
revisiting once there's real feedback on whether these labels hold up
across more job postings.

## Years-of-experience: never double-count overlap

`career/timeline.py::merge_intervals` merges overlapping/concurrent date
ranges before any duration is summed. This matters concretely for the
imported data: Riviana Foods (2020–present) and Edenred Pay (2022–2026)
overlap for ~3.5 years; OMS Online (2003–2008) and HedgeHog Marketing
Group (2004–2012) overlap for ~4 years. Both pairs are merged, not summed
— `careers experience`'s reported 26-year total career span is 26.37
calendar years, not the ~30 years you'd get by naively adding every role's
duration.

Per-skill estimates (`years_for_skill`) only count **dated** evidence
(tied to a `CareerRole` via a `RoleFraming` bullet). Evidence from a
skills-table row or a "Selected ... Work" project bullet has no anchored
date range (see docs/resume-model.md) and contributes existence (can
satisfy `strong_match`) but not duration — shown honestly as "unknown
(mentioned but not tied to a dated role)" rather than guessed at.

Display is deliberately bucketed (`career/timeline.py::humanize_years`:
"under 1 year", "1-2 years", "3-5 years", "6-9 years", "10+ years") instead
of false-precision like "6.42 years".

## Resume-variant recommendation

`career/resume_recommendation.py` scores each resume variant by summing
match-type weights (strong=1.0, partial=0.6, adjacent=0.35) across every
requirement where that variant's evidence was actually cited
(`EvidenceRef.variant_slug`), and recommends the highest-scoring one. The
reason given lists the specific requirements that drove the decision — a
direct readout of the scoring, not a separate explanation generator.

## AI guardrails

**Experience-fit matching in Phase 2 is entirely deterministic.** No AI
call decides whether a requirement is matched, and no AI call can turn an
`unsupported` gap into a match. This is enforced structurally, not just by
convention: `scoring/evidence_matcher.py` has no import of, or dependency
on, `scoring/ai.py` (see `tests/test_ai_guardrails.py`). The existing
Phase 1 AI layer (optional, `ANTHROPIC_API_KEY`-gated) only ever refines
`role_fit`/`career_direction_fit` — `scoring/ai.py::apply_ai_evaluation`
reads exactly two fields from its output and ignores everything else,
which `tests/test_ai_guardrails.py::TestAICannotTouchExperienceFit`
verifies directly by feeding it a fake result that also tries to override
experience data.

This means definition-of-done item "optional Claude refinement cannot
invent unsupported experience" is satisfied by construction — Claude is
never consulted about experience/evidence matching at all in Phase 2. The
spec's fuller vision (an AI layer that reasons about adjacent-experience
similarity or explains transferable experience, required to cite evidence
IDs and never infer un-cited technology) is real, valuable, and
deliberately **not built yet** — see docs/architecture.md's Phase 3
recommendations. Building a partial version of it now would have meant
either loosening the "never invent experience" guarantee or shipping
guardrail code with nothing yet to guard.
