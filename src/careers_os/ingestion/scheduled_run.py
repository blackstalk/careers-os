"""Scheduled discovery + alerting pipeline (Phase 4).

Reuses `ingestion/discovery.py::run_discovery` unchanged for search ->
normalize -> dedupe -> score -> evaluate; this module adds exactly one
new responsibility on top: deciding which already-evaluated opportunities
are alert-worthy, persisting that decision, checking for a prior alert,
and notifying through a `NotificationChannel` if not dry-run. See
docs/alerts.md.

This is the one place allowed to know about discovery, evaluation
persistence, alert policy, and a notification channel all at once —
mirroring how ingestion/pipeline.py and ingestion/discovery.py are each
the one place that knows about their own layer's full set of concerns.
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
from careers_os.domain.enums import EmploymentType
from careers_os.domain.job import format_compensation
from careers_os.domain.opportunity_decision import PursueRecommendation
from careers_os.ingestion.discovery import DiscoveryOpportunity, run_discovery
from careers_os.ingestion.evaluation import EvaluationResult
from careers_os.notifications.channel import NotificationChannel
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
    notifications_attempted: int = 0
    notifications_sent: int = 0
    notifications_suppressed_duplicate: int = 0
    failures: int = 0


@dataclass
class AlertOutcome:
    opportunity: DiscoveryOpportunity
    content: AlertContent
    status: str  # "sent" | "failed" | "suppressed_duplicate" | "dry_run"
    error: Optional[str] = None


@dataclass
class ScheduledRunResult:
    metrics: ScheduledRunMetrics = field(default_factory=ScheduledRunMetrics)
    alerts: list[AlertOutcome] = field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    notes: list[str] = field(default_factory=list)


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

    for opp in discovery_result.opportunities:
        decision = opp.decision
        if decision.eligibility.status == EligibilityStatus.ELIGIBLE:
            result.metrics.eligible_count += 1
        if decision.pursue.recommendation in (PursueRecommendation.PURSUE, PursueRecommendation.STRONG_PURSUE):
            result.metrics.pursue_threshold_count += 1

        if not should_alert(decision, policy, mode):
            continue
        result.metrics.alert_threshold_count += 1

        # Persist the evaluation now that it's actually alert-relevant —
        # scheduled runs don't persist every discovered job's evaluation,
        # only ones that crossed the threshold (see docs/alerts.md).
        evaluation_record = repository.save_evaluation(opp.job.id, decision)

        prior_sent = repository.list_notifications(opp.job.id, status="sent")
        current_rank = alert_rank(decision.pursue.recommendation)
        already_covered = any(
            alert_rank(PursueRecommendation(n.pursue_recommendation)) >= current_rank
            for n in prior_sent
        )

        content = build_alert_content(
            company=opp.job.company or "", role=opp.job.title,
            location=opp.job.location or "-", remote_status=opp.job.remote_status,
            compensation=format_compensation(opp.job.salary_min, opp.job.salary_max, opp.job.hourly_min, opp.job.hourly_max),
            source=opp.job.source, url=opp.job.source_url,
            result=EvaluationResult(decision=decision, fit=opp.fit, bridge=opp.bridge, immediate=opp.immediate, direction=opp.direction),
        )

        if already_covered:
            result.metrics.notifications_suppressed_duplicate += 1
            result.alerts.append(AlertOutcome(opportunity=opp, content=content, status="suppressed_duplicate"))
            continue

        if dry_run:
            result.alerts.append(AlertOutcome(opportunity=opp, content=content, status="dry_run"))
            continue

        result.metrics.notifications_attempted += 1

        if channel is None:
            result.metrics.failures += 1
            result.alerts.append(
                AlertOutcome(opportunity=opp, content=content, status="failed", error="No notification channel configured.")
            )
            continue

        send_result = channel.send(content)
        if send_result.success:
            repository.record_notification(
                opp.job.id, evaluation_id=evaluation_record.id, channel=channel.name,
                operating_mode=mode.value, pursue_recommendation=decision.pursue.recommendation.value,
                status="sent",
            )
            result.metrics.notifications_sent += 1
            result.alerts.append(AlertOutcome(opportunity=opp, content=content, status="sent"))
        else:
            repository.record_notification(
                opp.job.id, evaluation_id=evaluation_record.id, channel=channel.name,
                operating_mode=mode.value, pursue_recommendation=decision.pursue.recommendation.value,
                status="failed", error=send_result.error,
            )
            result.metrics.failures += 1
            result.alerts.append(
                AlertOutcome(opportunity=opp, content=content, status="failed", error=send_result.error)
            )

    repository.commit()
    result.completed_at = datetime.now(timezone.utc)
    logger.info(
        "scheduled_run.completed",
        extra={
            "jobs_discovered": result.metrics.jobs_discovered,
            "jobs_evaluated": result.metrics.jobs_evaluated,
            "eligible_count": result.metrics.eligible_count,
            "pursue_threshold_count": result.metrics.pursue_threshold_count,
            "alert_threshold_count": result.metrics.alert_threshold_count,
            "notifications_attempted": result.metrics.notifications_attempted,
            "notifications_sent": result.metrics.notifications_sent,
            "notifications_suppressed_duplicate": result.metrics.notifications_suppressed_duplicate,
            "failures": result.metrics.failures,
        },
    )
    return result
