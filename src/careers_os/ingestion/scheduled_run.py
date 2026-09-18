"""Scheduled discovery + alerting pipeline (Phase 4, extended in 4.1).

Reuses `ingestion/discovery.py::run_discovery` unchanged for search ->
normalize -> dedupe -> score -> evaluate; this module adds two
responsibilities on top: (1) deciding which already-evaluated
opportunities are alert-*eligible* (Phase 4), and (2) deciding which
*subset* of those eligible opportunities actually gets a notification
this run (Phase 4.1) — see docs/alerts.md.

Detection and notification selection are kept as distinct steps in this
function on purpose: `should_alert` (notifications/policy.py) decides
eligibility from the existing pursue-worthiness decision alone; grouping
near-duplicate postings (notifications/clustering.py) and capping how
many representatives get sent (`max_alerts_per_run`) never changes
whether an opportunity is *eligible* — only how many eligible ones
interrupt you right now. Every opportunity that crosses the pursue
threshold still gets its evaluation persisted regardless of whether it's
selected, deferred, or clustered away — see the first loop below.

This is the one place allowed to know about discovery, evaluation
persistence, alert policy, clustering, and a notification channel all at
once — mirroring how ingestion/pipeline.py and ingestion/discovery.py are
each the one place that knows about their own layer's full set of
concerns.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from careers_os.career.applications import ApplicationLog
from careers_os.career.candidate import CandidateProfile
from careers_os.career.preferences import Preferences
from careers_os.career.profile import CareerProfile
from careers_os.career.search_profiles import SearchProfile
from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.eligibility import EligibilityStatus
from careers_os.domain.enums import EmploymentType, OperatingMode
from careers_os.domain.job import format_compensation
from careers_os.domain.opportunity_decision import PursueRecommendation
from careers_os.domain.qualification import QualificationStatus
from careers_os.ingestion.ai_refinement import AIRefinementStats, refine_finalists
from careers_os.ingestion.discovery import DiscoveryOpportunity, run_discovery
from careers_os.ingestion.evaluation import EvaluationResult
from careers_os.notifications.channel import NotificationChannel
from careers_os.notifications.clustering import OpportunityCluster, cluster_opportunities, rank_clusters
from careers_os.notifications.content import AlertContent, build_alert_content
from careers_os.notifications.policy import alert_rank, should_alert
from careers_os.sources.authority import is_aggregator
from careers_os.sources.base import JobSource
from careers_os.storage.repository import JobRepository

logger = logging.getLogger("careers_os.ingestion.scheduled_run")

# `jobs discover`'s `limit` truncates the *displayed* results; a scheduled
# run must consider every discovered opportunity for alerting, not just a
# display-sized top slice. This is a call-site choice, not a change to
# run_discovery's own (correct, unrelated) default behavior.
_EFFECTIVELY_UNLIMITED = 10_000


@dataclass
class ScheduledRunMetrics:
    jobs_discovered: int = 0
    jobs_evaluated: int = 0
    eligible_count: int = 0
    pursue_threshold_count: int = 0  # recommendation is pursue or strong_pursue
    alert_threshold_count: int = 0  # crosses the *configured* policy threshold for the current mode

    # Phase 4.1 — notification selection, applied only to opportunities
    # already counted in alert_threshold_count above. See
    # docs/alerts.md#alert-prioritization-and-opportunity-clustering.
    opportunity_clusters: int = 0  # distinct families among alert-eligible opportunities
    clustered_variant_count: int = 0  # non-representative postings absorbed into a family
    alert_budget: int = 0  # this run's configured max_alerts_per_run
    alerts_selected: int = 0  # cluster representatives within budget (attempted, not necessarily sent)
    alerts_deferred: int = 0  # cluster representatives beyond budget — still alert-eligible next run

    # Phase 4.2 — alert-eligible opportunities removed before selection.
    applications_suppressed: int = 0  # already applied to / ruled out (applications.yaml or job status)
    superseded_by_authoritative: int = 0  # aggregator copy of a job whose ATS posting was also found

    # Phase 4.3 — bounded AI review of the opportunities above the alert
    # threshold (see ingestion/ai_refinement.py). alert_threshold_count is
    # counted *after* this review; this is the count before it.
    deterministic_alert_threshold_count: int = 0
    ai_refinement: AIRefinementStats = field(default_factory=AIRefinementStats)

    notifications_attempted: int = 0
    notifications_sent: int = 0
    notifications_suppressed_duplicate: int = 0
    failures: int = 0

    sources: dict[str, "SourceFunnel"] = field(default_factory=dict)


@dataclass
class SourceFunnel:
    """One source family's contribution to this run, for `jobs run` output."""

    raw: int = 0
    new: int = 0
    parse_errors: int = 0
    failures: list[str] = field(default_factory=list)
    unique_jobs: int = 0
    unique_to_source: int = 0  # no flagged duplicate from any other source
    eligible: int = 0
    qualified: int = 0  # strong or moderate qualification
    pursue_or_better: int = 0
    alert_threshold: int = 0


@dataclass
class AlertOutcome:
    opportunity: DiscoveryOpportunity
    content: AlertContent
    status: str  # "sent" | "failed" | "suppressed_duplicate" | "dry_run" | "deferred" | "already_applied" | "superseded"
    error: Optional[str] = None
    related_variant_count: int = 0


@dataclass
class ScheduledRunResult:
    metrics: ScheduledRunMetrics = field(default_factory=ScheduledRunMetrics)
    alerts: list[AlertOutcome] = field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    notes: list[str] = field(default_factory=list)


_CLOSED_JOB_STATUSES = {"applied", "interviewing", "offer", "rejected", "closed"}


def _content(opp: DiscoveryOpportunity, related_variant_count: int = 0) -> AlertContent:
    job = opp.job
    return build_alert_content(
        company=job.company or "", role=job.title,
        location=job.location or "-", remote_status=job.remote_status,
        compensation=format_compensation(job.salary_min, job.salary_max, job.hourly_min, job.hourly_max),
        source=job.source, url=job.source_url,
        result=EvaluationResult(decision=opp.decision, fit=opp.fit, bridge=opp.bridge, immediate=opp.immediate, direction=opp.direction),
        related_variant_count=related_variant_count,
    )


def _already_applied(opp: DiscoveryOpportunity, repository: JobRepository, applications: ApplicationLog) -> bool:
    """Applied to, or ruled out, per the committed applications.yaml or this
    database's own job status — for this job or any flagged duplicate."""
    job_ids = {opp.job.id, *repository.duplicate_job_ids(opp.job.id)}
    for job_id in job_ids:
        job = opp.job if job_id == opp.job.id else repository.session.get(type(opp.job), job_id)
        if job is None:
            continue
        if job.status in _CLOSED_JOB_STATUSES:
            return True
        if applications.match(url=job.source_url, company=job.company, title=job.title):
            return True
    return False


def _record_source_funnel(result: "ScheduledRunResult", discovery_result, repository: JobRepository) -> None:
    dm = discovery_result.metrics
    funnels = result.metrics.sources
    for family in set(dm.raw_by_source) | set(dm.failures_by_source):
        funnels[family] = SourceFunnel(
            raw=dm.raw_by_source.get(family, 0),
            new=dm.new_by_source.get(family, 0),
            parse_errors=dm.parse_errors_by_source.get(family, 0),
            failures=list(dm.failures_by_source.get(family, [])),
        )
    source_of = {o.job.id: o.job.source for o in discovery_result.opportunities}
    for opp in discovery_result.opportunities:
        f = funnels.setdefault(opp.job.source, SourceFunnel())
        f.unique_jobs += 1
        dup_sources = {
            source_of.get(d) or getattr(repository.session.get(type(opp.job), d), "source", None)
            for d in repository.duplicate_job_ids(opp.job.id)
        }
        if not (dup_sources - {opp.job.source, None}):
            f.unique_to_source += 1
        d = opp.decision
        if d.eligibility.status == EligibilityStatus.ELIGIBLE:
            f.eligible += 1
        if d.qualification.status in (QualificationStatus.STRONG, QualificationStatus.MODERATE):
            f.qualified += 1
        if d.pursue.recommendation in (PursueRecommendation.PURSUE, PursueRecommendation.STRONG_PURSUE):
            f.pursue_or_better += 1


def _handle_cluster(
    cluster: OpportunityCluster,
    *,
    selected: bool,
    evaluation_ids: dict[int, int],
    repository: JobRepository,
    mode: OperatingMode,
    dry_run: bool,
    channel: Optional[NotificationChannel],
    result: "ScheduledRunResult",
) -> None:
    """Decide the outcome for one cluster's representative — the only
    opportunity in its family considered for notification this run.
    Non-representative variants are never touched here (see
    `cluster.additional_variant_count`): they stay fully stored, scored,
    and inspectable, just not individually alerted.

    `selected=False` means this cluster ranked beyond `max_alerts_per_run`
    — it is recorded as "deferred" and, critically, `record_notification`
    is never called for it, so it remains alert-eligible in a future run
    (see docs/alerts.md#alert-prioritization-and-opportunity-clustering).
    """
    opp = cluster.representative
    decision = opp.decision
    related_variant_count = cluster.additional_variant_count
    content = _content(opp, related_variant_count)

    def _outcome(status: str, error: Optional[str] = None) -> AlertOutcome:
        return AlertOutcome(
            opportunity=opp, content=content, status=status, error=error,
            related_variant_count=related_variant_count,
        )

    if not selected:
        result.alerts.append(_outcome("deferred"))
        return

    # A duplicate of this job (e.g. the same posting found on another
    # source) that was already emailed counts as this job being emailed.
    prior_sent = [
        n
        for job_id in {opp.job.id, *repository.duplicate_job_ids(opp.job.id)}
        for n in repository.list_notifications(job_id, status="sent")
    ]
    current_rank = alert_rank(decision.pursue.recommendation)
    already_covered = any(
        alert_rank(PursueRecommendation(n.pursue_recommendation)) >= current_rank
        for n in prior_sent
    )

    if already_covered:
        result.metrics.notifications_suppressed_duplicate += 1
        result.alerts.append(_outcome("suppressed_duplicate"))
        return

    if dry_run:
        result.alerts.append(_outcome("dry_run"))
        return

    result.metrics.notifications_attempted += 1
    evaluation_id = evaluation_ids[opp.job.id]

    if channel is None:
        result.metrics.failures += 1
        result.alerts.append(_outcome("failed", error="No notification channel configured."))
        return

    send_result = channel.send(content)
    if send_result.success:
        repository.record_notification(
            opp.job.id, evaluation_id=evaluation_id, channel=channel.name,
            operating_mode=mode.value, pursue_recommendation=decision.pursue.recommendation.value,
            status="sent",
        )
        result.metrics.notifications_sent += 1
        result.alerts.append(_outcome("sent"))
    else:
        repository.record_notification(
            opp.job.id, evaluation_id=evaluation_id, channel=channel.name,
            operating_mode=mode.value, pursue_recommendation=decision.pursue.recommendation.value,
            status="failed", error=send_result.error,
        )
        result.metrics.failures += 1
        result.alerts.append(_outcome("failed", error=send_result.error))


def run_scheduled_pipeline(
    repository: JobRepository,
    sources: list[tuple[str, JobSource]],
    profiles: list[SearchProfile],
    *,
    dry_run: bool = False,
    channel: Optional[NotificationChannel] = None,
    remote_only: bool = False,
    employment_types: Optional[list[EmploymentType]] = None,
    use_ai: bool = True,
    career_profile: Optional[CareerProfile] = None,
    preferences: Optional[Preferences] = None,
    evidence_index=None,
    taxonomy: Optional[SkillsTaxonomy] = None,
    candidate: Optional[CandidateProfile] = None,
    applications: Optional[ApplicationLog] = None,
) -> ScheduledRunResult:
    started_at = datetime.now(timezone.utc)
    preferences = preferences or Preferences.load()
    mode = preferences.operating_mode
    policy = preferences.alert_policy
    applications = applications if applications is not None else ApplicationLog.load()

    logger.info("scheduled_run.started", extra={"operating_mode": mode.value, "dry_run": dry_run})

    # Bulk discovery is always deterministic; AI review happens below,
    # only for finalists and within budget.
    discovery_result = run_discovery(
        repository, sources, profiles,
        remote_only=remote_only, employment_types=employment_types, min_fit=None,
        limit=_EFFECTIVELY_UNLIMITED, use_ai=False, career_profile=career_profile,
        preferences=preferences, evidence_index=evidence_index, taxonomy=taxonomy,
        candidate=candidate,
    )

    result = ScheduledRunResult(
        metrics=ScheduledRunMetrics(
            jobs_discovered=discovery_result.metrics.raw_jobs_discovered,
            jobs_evaluated=discovery_result.metrics.unique_jobs,
        ),
        started_at=started_at,
        notes=list(discovery_result.notes),
    )

    _record_source_funnel(result, discovery_result, repository)

    if policy is None:
        result.notes.append("No alert_policy configured — evaluations ran, but no alerting was attempted.")
        result.completed_at = datetime.now(timezone.utc)
        logger.warning("scheduled_run.no_alert_policy_configured")
        return result

    # --- Detection: which opportunities cross the alert threshold deterministically? ---
    over_threshold: list[DiscoveryOpportunity] = []
    for opp in discovery_result.opportunities:
        decision = opp.decision
        if decision.eligibility.status == EligibilityStatus.ELIGIBLE:
            result.metrics.eligible_count += 1
        if decision.pursue.recommendation in (PursueRecommendation.PURSUE, PursueRecommendation.STRONG_PURSUE):
            result.metrics.pursue_threshold_count += 1
        if should_alert(decision, policy, mode):
            over_threshold.append(opp)
    result.metrics.deterministic_alert_threshold_count = len(over_threshold)

    # --- Removal before any AI spend: already applied, or an aggregator
    # copy of an employer posting found this run (Phase 4.2) ---
    authoritative_ids = {o.job.id for o in discovery_result.opportunities if not is_aggregator(o.job.source)}
    finalists: list[DiscoveryOpportunity] = []
    removed: list[tuple[DiscoveryOpportunity, str]] = []
    for opp in over_threshold:
        if _already_applied(opp, repository, applications):
            result.metrics.applications_suppressed += 1
            removed.append((opp, "already_applied"))
        elif is_aggregator(opp.job.source) and repository.duplicate_job_ids(opp.job.id) & authoritative_ids:
            result.metrics.superseded_by_authoritative += 1
            removed.append((opp, "superseded"))
        else:
            finalists.append(opp)

    # --- Bounded AI review of finalists, in alert-ranking order (Phase 4.3) ---
    if use_ai:
        review_order = [
            member
            for cluster in rank_clusters(cluster_opportunities(finalists))
            for member in [cluster.representative, *(v for v in cluster.variants if v is not cluster.representative)]
        ]
        result.metrics.ai_refinement = refine_finalists(
            review_order, repository=repository,
            budget=preferences.ai_refinement.max_refinements_per_run,
            stop_after_survivors=policy.for_mode(mode).max_alerts_per_run,
            survives=lambda opp: should_alert(opp.decision, policy, mode),
            profile=career_profile, preferences=preferences, candidate=candidate,
            taxonomy=taxonomy, evidence_index=evidence_index,
        )
    else:
        result.metrics.ai_refinement = AIRefinementStats(status="disabled (--no-ai)", finalists=len(finalists))

    # Persist the evaluation (after any AI review) for every opportunity
    # that crossed the threshold, regardless of whether it ends up
    # selected, deferred, clustered away, or demoted by the review —
    # evaluation history is orthogonal to notification selection.
    evaluation_ids: dict[int, int] = {}
    for opp in over_threshold:
        evaluation_ids[opp.job.id] = repository.save_evaluation(opp.job.id, opp.decision).id
    for opp, status in removed:
        result.alerts.append(AlertOutcome(opportunity=opp, content=_content(opp), status=status))

    alert_eligible = [opp for opp in finalists if should_alert(opp.decision, policy, mode)]
    for opp in [*alert_eligible, *(o for o, _ in removed)]:
        result.metrics.alert_threshold_count += 1
        result.metrics.sources.setdefault(opp.job.source, SourceFunnel()).alert_threshold += 1

    # --- Selection: which eligible opportunities actually get notified? (Phase 4.1) ---
    clusters = rank_clusters(cluster_opportunities(alert_eligible))
    budget = policy.for_mode(mode).max_alerts_per_run

    result.metrics.opportunity_clusters = len(clusters)
    result.metrics.clustered_variant_count = len(alert_eligible) - len(clusters)
    result.metrics.alert_budget = budget
    result.metrics.alerts_selected = min(budget, len(clusters))
    result.metrics.alerts_deferred = max(len(clusters) - budget, 0)

    for rank, cluster in enumerate(clusters):
        _handle_cluster(
            cluster, selected=rank < budget, evaluation_ids=evaluation_ids,
            repository=repository, mode=mode, dry_run=dry_run, channel=channel, result=result,
        )

    repository.commit()

    # Collapse this run's (and any prior runs') redundant raw_jobs/
    # job_scores history down to one row per job — see
    # JobRepository.prune_history for why this accumulates so fast.
    # Independent of dry_run: this cleans up discovery/scoring side
    # effects that already happen regardless of dry_run (see
    # run_discovery), never touches notification/alert state.
    prune_stats = repository.prune_history()
    logger.info("scheduled_run.pruned_history", extra=prune_stats)

    result.completed_at = datetime.now(timezone.utc)
    logger.info(
        "scheduled_run.completed",
        extra={
            "jobs_discovered": result.metrics.jobs_discovered,
            "jobs_evaluated": result.metrics.jobs_evaluated,
            "eligible_count": result.metrics.eligible_count,
            "pursue_threshold_count": result.metrics.pursue_threshold_count,
            "alert_threshold_count": result.metrics.alert_threshold_count,
            "opportunity_clusters": result.metrics.opportunity_clusters,
            "clustered_variant_count": result.metrics.clustered_variant_count,
            "alert_budget": result.metrics.alert_budget,
            "alerts_selected": result.metrics.alerts_selected,
            "alerts_deferred": result.metrics.alerts_deferred,
            "notifications_attempted": result.metrics.notifications_attempted,
            "notifications_sent": result.metrics.notifications_sent,
            "notifications_suppressed_duplicate": result.metrics.notifications_suppressed_duplicate,
            "applications_suppressed": result.metrics.applications_suppressed,
            "superseded_by_authoritative": result.metrics.superseded_by_authoritative,
            "deterministic_alert_threshold_count": result.metrics.deterministic_alert_threshold_count,
            "ai_refinement_status": result.metrics.ai_refinement.status,
            "ai_refinement_calls": result.metrics.ai_refinement.calls,
            "ai_refinement_reused": result.metrics.ai_refinement.reused,
            "ai_refinement_changed": len(result.metrics.ai_refinement.changed),
            "failures": result.metrics.failures,
        },
    )
    return result
