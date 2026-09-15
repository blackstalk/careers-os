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

No cloud infrastructure is introduced for this. `jobs run` is a plain,
idempotent CLI invocation, so any conventional OS scheduler works;
**macOS `launchd`** is recommended for a personal Mac setup over cron
because it can run at login/wake in addition to a fixed calendar time and
is the standard mechanism for this on macOS. See
`scripts/com.careersos.scheduledrun.plist.example` for a template that
runs `jobs run` once each weekday morning.

To install:

```
cp scripts/com.careersos.scheduledrun.plist.example \
   ~/Library/LaunchAgents/com.careersos.scheduledrun.plist
# edit the copied plist: absolute paths to the venv's python, this
# repo, and a log file destination
launchctl load ~/Library/LaunchAgents/com.careersos.scheduledrun.plist
```

**Known limitation:** a `launchd` job only runs while the Mac is powered
on and awake (or wakes it, if `StartCalendarInterval` combined with
`launchd`'s wake support is configured, which is not guaranteed on
battery). A run scheduled for a moment the machine is asleep or off is
skipped, not queued — it simply doesn't happen that day. This is
acceptable for a personal, passive-mode radar (a missed weekday run is
not a materially different outcome than the mode's own selectivity
already produces) but is the reason the pipeline is deliberately
structured so that moving to an always-on hosted runner (e.g. a small
scheduled GitHub Actions workflow or a cron on a small VM) later needs
only a new place to invoke `jobs run` — no change to
`ingestion/scheduled_run.py`, the alert policy, or the email channel.

## Observability

Every run logs (`logger = logging.getLogger("careers_os.ingestion.scheduled_run")`)
a start event and a completion event carrying jobs discovered/evaluated,
eligible count, pursue/alert-threshold counts, and
attempted/sent/suppressed/failure counts — no opportunity content or
credential values. A failed notification is recorded as `status="failed"`
with the channel's own error text and never marks the opportunity as
alerted; it also never rolls back or corrupts the opportunity's stored
evaluation, since the evaluation is saved (`repository.save_evaluation`)
independently of, and before, the send attempt.
