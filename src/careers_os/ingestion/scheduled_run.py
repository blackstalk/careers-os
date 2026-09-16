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

from careers_os.career.candidate import CandidateProfile
from careers_os.career.preferences import Preferences
from careers_os.career.profile import CareerProfile
from careers_os.career.search_profiles import SearchProfile
from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.eligibility import EligibilityStatus
from careers_os.domain.enums import EmploymentType, OperatingMode
from careers_os.domain.job import format_compensation
from careers_os.domain.opportunity_decision import PursueRecommendation
from careers_os.ingestion.discovery import DiscoveryOpportunity, run_discovery
from careers_os.ingestion.evaluation import EvaluationResult
from careers_os.notifications.channel import NotificationChannel
from careers_os.notifications.clustering import OpportunityCluster, cluster_opportunities, rank_clusters
from careers_os.notifications.content import AlertContent, build_alert_content
from careers_os.notifications.policy import alert_rank, should_alert
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

    notifications_attempted: int = 0
    notifications_sent: int = 0
    notifications_suppressed_duplicate: int = 0
    failures: int = 0


@dataclass
class AlertOutcome:
    opportunity: DiscoveryOpportunity
    content: AlertContent
    status: str  # "sent" | "failed" | "suppressed_duplicate" | "dry_run" | "deferred"
    error: Optional[str] = None
    related_variant_count: int = 0


@dataclass
class ScheduledRunResult:
    metrics: ScheduledRunMetrics = field(default_factory=ScheduledRunMetrics)
    alerts: list[AlertOutcome] = field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    notes: list[str] = field(default_factory=list)


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
    content = build_alert_content(
        company=opp.job.company or "", role=opp.job.title,
        location=opp.job.location or "-", remote_status=opp.job.remote_status,
        compensation=format_compensation(opp.job.salary_min, opp.job.salary_max, opp.job.hourly_min, opp.job.hourly_max),
        source=opp.job.source, url=opp.job.source_url,
        result=EvaluationResult(decision=decision, fit=opp.fit, bridge=opp.bridge, immediate=opp.immediate, direction=opp.direction),
        related_variant_count=related_variant_count,
    )

    def _outcome(status: str, error: Optional[str] = None) -> AlertOutcome:
        return AlertOutcome(
            opportunity=opp, content=content, status=status, error=error,
            related_variant_count=related_variant_count,
        )

    if not selected:
        result.alerts.append(_outcome("deferred"))
        return

    prior_sent = repository.list_notifications(opp.job.id, status="sent")
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
) -> ScheduledRunResult:
    started_at = datetime.now(timezone.utc)
    preferences = preferences or Preferences.load()
    mode = preferences.operating_mode
    policy = preferences.alert_policy

    logger.info("scheduled_run.started", extra={"operating_mode": mode.value, "dry_run": dry_run})

    discovery_result = run_discovery(
        repository, sources, profiles,
        remote_only=remote_only, employment_types=employment_types, min_fit=None,
        limit=_EFFECTIVELY_UNLIMITED, use_ai=use_ai, career_profile=career_profile,
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

    if policy is None:
        result.notes.append("No alert_policy configured — evaluations ran, but no alerting was attempted.")
        result.completed_at = datetime.now(timezone.utc)
        logger.warning("scheduled_run.no_alert_policy_configured")
        return result

    # --- Detection: which opportunities are alert-*eligible*? (Phase 4, unchanged) ---
    alert_eligible: list[DiscoveryOpportunity] = []
    evaluation_ids: dict[int, int] = {}
    for opp in discovery_result.opportunities:
        decision = opp.decision
        if decision.eligibility.status == EligibilityStatus.ELIGIBLE:
            result.metrics.eligible_count += 1
        if decision.pursue.recommendation in (PursueRecommendation.PURSUE, PursueRecommendation.STRONG_PURSUE):
            result.metrics.pursue_threshold_count += 1

        if not should_alert(decision, policy, mode):
            continue
        result.metrics.alert_threshold_count += 1

        # Persist the evaluation for every alert-eligible opportunity,
        # regardless of whether it ends up selected, deferred, or
        # clustered away below — evaluation history is orthogonal to
        # notification selection (see module docstring).
        evaluation_record = repository.save_evaluation(opp.job.id, decision)
        evaluation_ids[opp.job.id] = evaluation_record.id
        alert_eligible.append(opp)

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
            "failures": result.metrics.failures,
        },
    )
    return result
