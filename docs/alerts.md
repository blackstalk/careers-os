# Scheduled discovery and intelligent alerts (Phase 4)

`jobs run` turns Careers OS from a manually-invoked tool into a quiet,
continuously operating opportunity radar: discover → evaluate through the
existing Phase 1–3 decision pipeline (`ingestion/evaluation.py`,
docs/pursue-recommendation.md) → decide, per opportunity, whether it
clears your alert policy → email it if so, skipping anything already
alerted. See `ingestion/scheduled_run.py::run_scheduled_pipeline` for the
orchestration.

**Guiding principle: notifications are scarce.** A run that finds nothing
compelling finishes silently — no email, just a log line like "8
opportunities evaluated. No opportunities met passive alert threshold."
There is no such thing as a low-confidence or "just in case" alert.

## How the scheduled run works

`run_scheduled_pipeline` reuses `ingestion/discovery.py::run_discovery`
completely unchanged for search → normalize → dedupe → score → evaluate
(the only difference from `jobs discover` is that it's called with an
effectively unlimited result cap, since alerting must consider every
discovered opportunity, not just a display-sized top slice). On top of
that unchanged evaluation, this phase adds exactly one new
responsibility: deciding which evaluated opportunities are alert-worthy,
persisting that decision, checking for a prior alert, and notifying
through a `NotificationChannel` if not a dry run.

```
Opportunity discovered
  → existing Careers OS evaluation (eligibility, qualification,
    transferable fit, career direction, opportunity value, pursue)
  → alert policy (notifications/policy.py::should_alert)
  → notification channel (notifications/channel.py)
```

The evaluation layer knows nothing about alerting or email. The alert
policy knows nothing about SMTP. `EmailNotificationChannel` knows nothing
about pursue-worthiness — it only knows how to send an `AlertContent`
it's handed. Each layer can be tested, and later replaced, independently.

### Why this isn't a new/competing score

`notifications/policy.py::should_alert` is a threshold on the recommendation
already produced by `career/pursue.py` (docs/pursue-recommendation.md) — it
does not compute anything new. An opportunity is never alert-eligible
because of title keywords ("Solutions Architect", "Machine Learning"),
high compensation, or meeting many requirements in isolation; it's
alert-eligible only because the existing pursue-worthiness layer already
concluded `pursue` or `strong_pursue` (or higher, per the configured
threshold — see Operating Modes below). `verify_first` and
`do_not_pursue` are structurally excluded from the alert-rank table
(`notifications/policy.py::_ALERT_RANK`) — no threshold in either mode can
ever make them alert-eligible.

## Operating modes

`career/preferences.py::OperatingMode` — `passive` (default) or `active`,
set in `career/data/preferences.yaml`:

```yaml
operating_mode: passive

alert_policy:
  passive:
    minimum_pursue: strong_pursue
  active:
    minimum_pursue: pursue
```

| Mode | Alert floor | Intent |
|---|---|---|
| `passive` | `strong_pursue` | Highly selective — interrupt only for opportunities the existing intelligence layer already rates as the strongest tier. This is the current mode: still employed, not maximizing applications, transitioning toward FDE/Solutions Architect/ML Systems roles. |
| `active` | `pursue` | Looser floor for a future higher-volume job search. Still gated by the same pursue-worthiness layer and the same hard floor on `verify_first`/`do_not_pursue`. |

Switching modes is a one-line config change in `preferences.yaml` — no
code change, no redesign. Both mode thresholds are declared side by side
in the same file specifically so this switch stays a config edit.

## Alert content

`notifications/content.py::build_alert_content` assembles an `AlertContent`
from the same `EvaluationResult` `jobs evaluate` already produces — no new
evidence is computed for the email. The subject line follows `Careers OS:
Strong opportunity — [Company] / [Role]`. The body (see
`AlertContent.to_plain_text`) is ordered for a phone read: company, role,
location, work arrangement, compensation, source, and the opportunity URL
first, then the Careers OS intelligence summary (pursue recommendation,
eligibility, qualification, career-direction alignment, opportunity
value), then **"Why this was surfaced"** and **"Watchouts"**, then the
opportunity link again as the call to action.

"Why this was surfaced" and "Watchouts" are derived, never invented:
`_build_reasons`/`_build_watchouts` in `notifications/content.py` read
directly off the real bridge/immediate/direction assessments and
requirement-match evidence already computed upstream (e.g. a `VERIFY`
eligibility check becomes a watchout citing its own reason text; an
`interview_prep_gap` match becomes a watchout naming the specific skill;
an unpublished/low-confidence compensation figure becomes a watchout
rather than being silently treated as strong). If an evaluation is thin,
a small fallback reason is used so the list is never empty — see
`tests/test_alert_content.py::TestAlertContentReasons::test_reasons_never_empty`
— but nothing here fabricates a reason unsupported by the evaluation.

## Duplicate suppression

Every successfully sent alert is persisted as a `NotificationRecord`
(`storage/db.py`) carrying the job, the pursue recommendation at send
time, the operating mode, and the channel. Before sending, the pipeline
checks `repository.list_notifications(job_id, status="sent")`: if any
prior *successful* send exists at an equal-or-higher pursue tier
(`notifications/policy.py::alert_rank`), the new alert is suppressed as a
duplicate rather than sent again.

This one rule naturally covers both required cases without extra
machinery:

- **Same opportunity, same run-to-run re-discovery** — already alerted at
  `strong_pursue`, still `strong_pursue` next run → suppressed.
- **Material improvement** — previously alerted at `pursue`, later
  re-evaluates to `strong_pursue` → *not* suppressed, since the new rank
  exceeds the prior one. (The reverse — a later evaluation that drops back
  down — is naturally never re-alerted, since it wouldn't cross the
  configured threshold in the first place.)

A **failed** send is recorded with `status="failed"` for observability but
is never counted toward "already alerted" — a dropped SMTP connection
must not permanently suppress a real opportunity. See
`tests/test_scheduled_run.py` (`test_failed_send_does_not_suppress_retry`,
`test_failed_send_not_marked_as_alerted`).

## Alert prioritization and opportunity clustering (Phase 4.1)

Adding the Ashby/Lever sources (docs/source-evaluation.md) immediately
exposed a real problem: a single `jobs run --dry-run --source ashby`
against a large, genuinely well-matched company (OpenAI) surfaced 44
opportunities that legitimately crossed the passive `strong_pursue`
threshold in one run — mostly because a company posts many near-duplicate
variants of the same role (different team, region, or segment). Every one
of those 44 was individually correct; sending 44 emails at once is not.

### Detection vs. interruption

**Detection and notification selection are separate concerns.** Whether
an opportunity is *good* is decided entirely by the existing pipeline
(eligibility → qualification → transferable fit → career direction →
opportunity value → pursue-worthiness) and `should_alert`
(`notifications/policy.py`) — nothing in this section changes that, and
none of it exists to reduce how many opportunities are *found*. What's
new is a second question asked only of opportunities that already passed
detection: of everything eligible right now, which subset actually
deserves an interruption *this run*? The rest aren't discarded — they're
deferred, and remain fully eligible for a future run.

### Opportunity families

`notifications/clustering.py::cluster_opportunities` groups already
alert-eligible opportunities into families using only structured data
already on the job record — company, title, and the job's own location —
never a new model, embedding, or LLM call:

- Two opportunities cluster only if they share the same **company** and
  the same **title family** — a title with a trailing segment stripped
  when that segment is either a small, industry-generic qualifier
  (remote/hybrid/onsite, enterprise/startups/commercial, public
  sector/government, new grad/intern) or textually derived from the
  job's own `location` field (e.g. "Applied AI Architect - Tokyo"
  alongside a location of "Tokyo, Japan").
- Everything else about the title is left alone. A title like "Physical
  Design Engineer, Forward Deployed Engineering" is never truncated to
  "Physical Design Engineer" — nothing in the stripped-suffix vocabulary
  matches "Forward Deployed Engineering", so it's kept whole. Under-
  clustering (treating two related roles as separate) is the deliberate
  failure mode over over-clustering (incorrectly merging two different
  roles) — see `tests/test_clustering.py`.
- Company identity is part of the key, so the same title at two
  different companies never clusters, and provider identity
  (Greenhouse/Ashby/Lever) is never part of the key at all — a cluster
  forms or doesn't purely from company + title + location, regardless of
  which source discovered it.

**Clustering affects notification selection only.** It never deletes,
merges, or mutates an opportunity record. Every discovered job — cluster
representative or not — remains independently stored, scored via
`repository.save_evaluation`, and inspectable via `jobs show`/`jobs
evaluate` regardless of which family it was grouped into.

### Representative selection and ranking

Within a cluster, the strongest opportunity is picked as its
representative — "strongest" meaning the existing pursue tier first,
then the existing discovery `rank_score` (`career/discovery_ranking.py`,
already blending overall fit, bridge-role strength, immediate
opportunity, and career-direction alignment) as a tie-break. Cluster
representatives are then ranked against each other using that exact same
`(pursue tier, rank_score)` key — no new score, and no company/provider
identity is ever part of the ranking, so an OpenAI opportunity and a
Palantir opportunity of equal underlying strength rank identically.

### Alert budget

`AlertModeSettings.max_alerts_per_run` (`career/preferences.py`,
default `5`, configured per operating mode in `preferences.yaml`) caps
how many cluster representatives are actually attempted per run. Ranked
representatives beyond the budget are marked `"deferred"` — not sent, not
persisted as alerted, and therefore still fully alert-eligible next run.
The alert body itself mentions related variants when relevant
(`AlertContent.related_variant_count`, `notifications/content.py`):

```
Applied AI Engineer — OpenAI

Strong match.
...
4 additional closely related OpenAI opportunities were also discovered.
```

### Deferred opportunities and the state-management invariant

Crossing the pursue threshold makes an opportunity *eligible* for an
alert; it does not mean the opportunity *was* alerted. Only a cluster
representative that is both selected (within budget) and actually sent
ever gets a `NotificationRecord` with `status="sent"` — a deferred
representative, and every non-representative variant absorbed into any
cluster, never reaches `repository.record_notification` at all. This is
what lets a large first-run backlog (the original 44-opportunity case)
drain naturally over subsequent runs, and what lets the same opportunity
resurface if it's re-evaluated later, rather than being silently and
permanently treated as "already handled." See
`tests/test_alert_prioritization.py::TestDeferredStateInvariant` for the
tests proving this directly, including a deferred opportunity
successfully sending on a later run once budget allows it.

### Dry-run observability

`jobs run --dry-run` reports the full funnel so it's possible to see
exactly why, say, 44 strong candidates produced only 5 notifications:

```
Meeting alert threshold (passive mode): 44
Opportunity clusters/families: 12
Clustered variants (absorbed into a family): 32
Alert budget (passive mode): 5
Selected for alert: 5
Deferred (over budget): 7
```

`jobs health` (per-source connectivity diagnostics) was deliberately left
untouched — the alert budget and clustering results are run-level policy
output, not source health, and `jobs run`'s own header already surfaces
the active budget every time it runs.

## Email configuration

Email is sent over SMTP using only the standard library (`smtplib`,
`email.message.EmailMessage`) via `notifications/email_channel.py` — no
new dependency. `EmailNotificationChannel.from_env()` reads:

| Variable | Purpose |
|---|---|
| `SMTP_HOST` | SMTP server hostname (e.g. Zoho's `smtp.zoho.com`) |
| `SMTP_PORT` | SMTP port (587 for STARTTLS) |
| `SMTP_USERNAME` | SMTP auth username |
| `SMTP_PASSWORD` | SMTP auth password/app-specific password |
| `SMTP_FROM` | The `From:` address on outgoing alerts |
| `SMTP_USE_TLS` | `true`/`false` — STARTTLS toggle (default `true`) |
| `CAREERS_ALERT_EMAIL` | Where alerts are sent — your own inbox |

All required variable names are declared (empty) in `.env.example`. If any
of `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` /
`SMTP_FROM` / `CAREERS_ALERT_EMAIL` is missing, `from_env()` returns
`None` and the pipeline runs evaluation as usual but skips sending
(`jobs run` prints a yellow warning instead of failing the whole run).
Nothing in this codebase prints, logs, or persists a credential value —
only connection/auth failure text from the SMTP server itself is ever
surfaced, in `ChannelResult.error`.

To enable real sending, populate a local, git-ignored `.env` (or
`.env.local`) with real values for the variables above — this repository
never copies credentials from another repository automatically.

## Running manually

```
jobs run                # full scheduled pipeline: discover, evaluate, alert
jobs run --dry-run       # same evaluation, but never sends email or persists
                         # a NotificationRecord — shows exactly what would
                         # have been sent, and why
jobs run --profile fde-search --source greenhouse --remote --no-ai
jobs notifications test  # send one clearly-labeled test email to verify
                         # SMTP config end-to-end without needing a real
                         # discovered opportunity
```

`jobs run` prints: jobs discovered, jobs evaluated, eligible count,
count meeting the pursue threshold, count meeting the *current mode's*
alert threshold, notifications attempted/sent/suppressed-as-duplicate,
and failures — then, only if at least one opportunity crossed the alert
threshold, each alert outcome (`sent` / `failed` / `suppressed_duplicate`
/ `dry_run`). In dry-run mode, each `dry_run` outcome also prints the
subject line and the reasons/watchouts that would have been in the email
body — never the SMTP configuration itself.

`jobs notifications test` fails closed with a clear message
(`SMTP is not configured. Set SMTP_HOST, ...`) and a non-zero exit code
if SMTP isn't configured, rather than silently doing nothing or printing
any partial credential state.

## Scheduling

`jobs run` is a plain, idempotent CLI invocation — moving where it's
invoked from never requires touching `ingestion/scheduled_run.py`, the
alert policy, or the email channel. Two mechanisms exist:

### GitHub Actions (primary, recommended)

`.github/workflows/scheduled-run.yml` runs `jobs run` on GitHub's
hosted infrastructure weekdays at 13:00 UTC (8:00am US Central during
Daylight Time — GitHub Actions cron doesn't observe DST, so this drifts
an hour during Standard Time), plus supports a manual trigger
(`workflow_dispatch`, or `gh workflow run scheduled-run.yml`). This is
what actually solves the "my Mac might be asleep" problem the local
`launchd` approach below has — GitHub's runners are always available
regardless of your machine's state.

**State persistence**: GitHub-hosted runners start from a clean checkout
every run — nothing survives between runs by default. Since alert dedup
(`NotificationRecord`) lives in `data/careers.db`, the workflow's final
step commits that file back to the repo (`chore: update job database
from scheduled run [skip ci]`) whenever it changed, so the next run picks
up exactly where the last one left off. `data/careers.db` is deliberately
**not** git-ignored (see `.gitignore`'s `!data/careers.db` exception) for
this reason; only its transient `-journal`/`-wal`/`-shm` SQLite files
still are.

**AI evaluation**: the workflow passes `ANTHROPIC_API_KEY` through as a
repo secret, so scheduled runs use the same optional AI evidence-
reasoning layer (docs/pursue-recommendation.md) as a local run with that
key set — deterministic scoring/qualification/pursue logic is identical
either way; the key only affects the narrow set of ambiguous-evidence
gaps that layer is allowed to touch. Omitting the secret (or passing
`jobs run --no-ai` instead) falls back to fully deterministic scoring
with no behavior change beyond skipping that optional reasoning step.

**Required repo secrets** (Settings → Secrets and variables → Actions),
matching `.env.example`: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`,
`SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS`, `CAREERS_ALERT_EMAIL`, and
optionally `ANTHROPIC_API_KEY`. Set these directly in GitHub's UI, or
with the `gh` CLI reading from your own local `.env` (never pasted
through an assistant):

```bash
for var in SMTP_HOST SMTP_PORT SMTP_USERNAME SMTP_PASSWORD SMTP_FROM SMTP_USE_TLS CAREERS_ALERT_EMAIL ANTHROPIC_API_KEY; do
  value=$(grep "^${var}=" .env | cut -d= -f2-)
  gh secret set "$var" --body "$value"
done
```

**Repo size caveat**: `data/careers.db` grows over time (raw job
payloads, descriptions, evaluations, notification history) and gets
committed as a new binary blob each run — SQLite files don't delta well
in git. Fine to start with; worth revisiting (e.g. periodic pruning of
old `raw_jobs`, or migrating to GitHub Actions cache / an external store)
if the repo grows uncomfortably large.

**Running both this and local `launchd` at once will cause duplicate
alerts** — the two would each hold their own divergent copy of the dedup
state. Pick one as authoritative; see below for disabling `launchd`.

### macOS `launchd` (local alternative / manual testing)

For a personal Mac setup without GitHub Actions, `launchd` is preferred
over cron because it can run at login/wake in addition to a fixed
calendar time. See `scripts/com.careersos.scheduledrun.plist.example`
for a template that runs `jobs run` once each weekday morning.

To install:

```
cp scripts/com.careersos.scheduledrun.plist.example \
   ~/Library/LaunchAgents/com.careersos.scheduledrun.plist
# edit the copied plist: absolute paths to the venv's python, this
# repo, and a log file destination
launchctl load ~/Library/LaunchAgents/com.careersos.scheduledrun.plist
```

To uninstall (e.g. once GitHub Actions is the authoritative scheduler):

```
launchctl unload ~/Library/LaunchAgents/com.careersos.scheduledrun.plist
```

**Known limitation** — the reason GitHub Actions is now recommended
instead: a `launchd` job only runs while the Mac is powered on and awake
(or wakes it, if `StartCalendarInterval` combined with `launchd`'s wake
support is configured, which is not guaranteed on battery). A run
scheduled for a moment the machine is asleep or off is skipped, not
queued — it simply doesn't happen that day.

## Observability

Every run logs (`logger = logging.getLogger("careers_os.ingestion.scheduled_run")`)
a start event and a completion event carrying jobs discovered/evaluated,
eligible count, pursue/alert-threshold counts, the Phase 4.1 clustering/
budget counts (`opportunity_clusters`, `clustered_variant_count`,
`alert_budget`, `alerts_selected`, `alerts_deferred`), and
attempted/sent/suppressed/failure counts — no opportunity content or
credential values. A failed notification is recorded as `status="failed"`
with the channel's own error text and never marks the opportunity as
alerted; it also never rolls back or corrupts the opportunity's stored
evaluation, since the evaluation is saved (`repository.save_evaluation`)
independently of, and before, the send attempt — this holds regardless of
whether the opportunity was a cluster representative or deferred.
