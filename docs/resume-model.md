# Resume model

## Career truth vs. resume presentation

The system distinguishes two things that are easy to conflate:

- **Career truth** — `CareerRole`: one employer, one title, one date range.
  Identity is `(company, start_date)`, encoded into `CareerRole.id` at
  import time (`career/resume_import.py`). This is shared across every
  resume variant that mentions it.
- **Resume presentation** — `RoleFraming`: how one specific resume variant
  describes a given `CareerRole` (its bullets, verbatim, for that variant).

Two resumes can (and, for the two imported here, do) describe the exact
same `CareerRole` — same company, same title, same dates — with
completely different bullets. Importing both doesn't duplicate the
employment record; it attaches a second `RoleFraming` to the same
`CareerRole`. `storage/resume_repository.py::import_result` implements
this by upserting `CareerRoleRecord` by id (mirroring
`JobRepository.upsert_job`'s dedup pattern from Phase 1) while always
replacing that variant's own framings.

## What gets imported

From each `.docx` (`career/resume_import.py`, no LLM involved):

```
ResumeVariant       — one imported file (slug, positioning line, summary, file hash)
CareerRole          — one employer/title/date-range fact (career truth)
RoleFraming         — one variant's bullets for one CareerRole
ProjectEvidenceEntry — a "Selected Architecture/Portfolio Work" sub-project
Evidence            — one provenance-backed statement (from a bullet, a
                       project bullet, or a skills-table row)
```

## How the importer actually works

Built by inspecting two real resumes' actual Word paragraph styles in true
document order (`docx.oxml` body-child iteration — `python-docx`'s own
`.paragraphs`/`.tables` are separate, order-losing collections). Both
source documents use the same style set:

| Style | Meaning |
|---|---|
| `Title` | Candidate name (not needed structurally) |
| `Subtitle` | Positioning line, e.g. "Forward Deployed Engineer \| Solutions Architect \| Technical Systems Leader" |
| `Heading 1` | Section boundary (Professional Summary, Professional Experience, Selected Architecture Work, Technical Foundation, ...) |
| `Role Header` | Two lines in one paragraph: `Company \| Title` then `Location \| Date range` (or, under a "Selected ... Work" section, `Project Title \| Role Descriptor` then `Context \| Selected Initiative`) |
| `List Bullet` | One evidence-bearing bullet under the nearest preceding Role Header |
| `Normal` | Section-dependent: summary prose, a flat pipe-separated skills line, or a compact one-line `Company \| Title \| Year range` entry |

This is a **deterministic parser over a specific, verified structure** —
not a general resume parser. A resume using different styles produces
warnings (unparseable date ranges, bullets with no preceding header,
unrecognized `Role Header` sections) rather than silently wrong data; see
`ResumeImportResult.warnings`.

### A known, deliberate gap: project evidence has no employer link

"Selected Architecture Work" / "Selected Portfolio Work" sections read like
sub-projects of a specific employer (their content clearly maps to Riviana
and Edenred work), but the source documents never state that link
explicitly. `ProjectEvidenceEntry.related_career_role_id` and the
resulting `Evidence.provenance.company` are left `None` rather than
inferred from context — see the "do not manufacture evidence" principle in
docs/evidence-model.md. This means project-evidence bullets can still
serve as direct evidence a skill *was done* (for `strong_match` purposes),
but they don't contribute to that skill's dated years-of-experience
calculation, since there's no anchored date range to attach.

### Overlapping employment is expected, not an error

The importer makes no assumption that only one role is active at a time.
Both imported resumes describe genuinely overlapping employment (Riviana
Foods, 2020–present, concurrent with Edenred Pay, 2022–2026; and, further
back, OMS Online 2003–2008 concurrent with HedgeHog Marketing Group
2004–2012). Every `CareerRole` is kept as its own record; not
double-counting overlaps is handled downstream by
`career/timeline.py::merge_intervals`, never by refusing to import one of
the overlapping roles.

## Multiple resume variants in practice

Two variants were imported from the attached resumes:

- `fde` — "Forward Deployed Engineer / Solutions Architect / Technical
  Systems Leader" positioning. Bullets emphasize architecture decisions,
  platform strategy, and cross-functional/vendor coordination.
- `senior_dev` — "Senior Full Stack Developer / Craft CMS / WordPress / AI
  and Automation" positioning. Bullets for the *same* employers emphasize
  the concrete technology stack (Bedrock, ACF, Tailwind, Twig) and
  hands-on delivery.

Both reference the same 6 shared `CareerRole` records (Riviana, Edenred,
Fifth Ring, Pennebaker, Shared Vision Marketing, HedgeHog); `senior_dev`
additionally contributes 3 more roles from its "Earlier Technical
Experience" section (OMS Online, ManTech ADG, Gail Darling) that `fde`
omits — those are still real `CareerRole` facts once either variant has
been imported, because `CareerRole` identity isn't scoped to a variant.

See `career/resume_recommendation.py` and docs/evidence-model.md for how a
specific job's requirement matches are used to recommend which variant to
actually use.
