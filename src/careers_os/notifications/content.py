"""Alert content assembly (Phase 4).

Every field below is read from something Careers OS already computed —
`ingestion.evaluation.EvaluationResult` and the job record it was
evaluated from. Nothing here infers, scores, or invents; a requirement
match, an eligibility check, or a component reason either exists in the
evaluation already or it isn't included. See docs/alerts.md#alert-content.
"""

from dataclasses import dataclass, field

from careers_os.career.bridge_role import BridgeClassification
from careers_os.career.opportunity_value import OpportunityLevel
from careers_os.domain.matching import GapType
from careers_os.domain.opportunity_decision import Freshness, OpportunityCostLevel, ScopeDimension
from careers_os.domain.qualification import QualificationStatus
from careers_os.ingestion.evaluation import EvaluationResult

_MAX_REASONS = 5
_MAX_WATCHOUTS = 5
_LOW_SCOPE_DIMENSIONS = {ScopeDimension.EXECUTION, ScopeDimension.MAINTENANCE}
_COMP_CONFIDENCE_THRESHOLD = 0.3
_COMP_STRONG_SCORE = 0.75


@dataclass
class AlertContent:
    company: str
    role: str
    location: str
    work_arrangement: str
    compensation: str
    source: str
    url: str

    pursue: str
    eligibility: str
    qualification: str
    career_direction: str
    opportunity_value: str

    reasons: list[str] = field(default_factory=list)
    watchouts: list[str] = field(default_factory=list)

    # Phase 4.1: how many other discovered postings were grouped into
    # this same opportunity family (same company, closely related title —
    # see notifications/clustering.py) and therefore didn't get their own
    # separate alert this run. Purely informational — those opportunities
    # remain fully stored/scored/inspectable, this just tells the reader
    # they exist. 0 means no clustering happened for this alert.
    related_variant_count: int = 0

    @property
    def subject(self) -> str:
        return f"Careers OS: {self.pursue.replace('_', ' ').title()} opportunity — {self.company} / {self.role}"

    def to_plain_text(self) -> str:
        lines = [
            f"{self.role} — {self.company}",
            f"Location: {self.location}  |  Work arrangement: {self.work_arrangement}",
            f"Compensation: {self.compensation}",
            f"Source: {self.source}",
            "",
            f"Pursue: {self.pursue.replace('_', ' ').upper()}",
            f"Eligibility: {self.eligibility}",
            f"Qualification: {self.qualification}",
            f"Career direction: {self.career_direction}",
            f"Opportunity value: {self.opportunity_value}",
            "",
            "Why this was surfaced:",
        ]
        lines += [f"  - {r}" for r in self.reasons]
        if self.watchouts:
            lines += ["", "Watchouts:"]
            lines += [f"  - {w}" for w in self.watchouts]
        if self.related_variant_count > 0:
            plural = "s" if self.related_variant_count != 1 else ""
            lines += [
                "",
                f"{self.related_variant_count} additional closely related {self.company} "
                f"opportunit{'ies' if plural else 'y'} {'were' if plural else 'was'} also discovered.",
            ]
        lines += ["", f"View Opportunity: {self.url}"]
        return "\n".join(lines)


def _build_reasons(result: EvaluationResult) -> list[str]:
    reasons: list[str] = []
    decision = result.decision
    detail = result.fit.experience_detail

    if result.bridge.classification == BridgeClassification.STRONG:
        reasons.append(f"Strong bridge toward target direction: {result.bridge.reason}")
    if result.direction.level == OpportunityLevel.STRONG:
        reasons.append(f"Career direction alignment: {result.direction.reason}")
    if detail is not None and detail.strong_matches:
        names = ", ".join(m.requirement.text for m in detail.strong_matches[:5])
        reasons.append(f"{len(detail.strong_matches)} strong requirement match(es): {names}")
    if (
        decision.qualification.status in (QualificationStatus.STRONG, QualificationStatus.MODERATE)
        and not decision.qualification.hard_gaps
    ):
        reasons.append("No hard-requirement gaps detected")
    comp = result.fit.compensation_fit
    if comp.confidence >= _COMP_CONFIDENCE_THRESHOLD and comp.score >= _COMP_STRONG_SCORE:
        reasons.append(f"Compensation: {comp.reason}")
    if decision.opportunity_cost.level == OpportunityCostLevel.LOW:
        reasons.append(f"Opportunity cost is low: {decision.opportunity_cost.reason}")
    if result.immediate.level == OpportunityLevel.STRONG:
        reasons.append(f"Immediate opportunity: {result.immediate.reason}")

    if not reasons:
        reasons.append(
            f"Crossed the pursue-worthiness threshold ({decision.pursue.recommendation.value}) "
            "for your current operating mode."
        )
    return reasons[:_MAX_REASONS]


def _build_watchouts(result: EvaluationResult) -> list[str]:
    watchouts: list[str] = []
    decision = result.decision
    detail = result.fit.experience_detail

    for check in decision.eligibility.verify_checks + decision.eligibility.unknown_checks:
        watchouts.append(f"Eligibility needs verification — {check.requirement}: {check.reason}")

    if detail is not None:
        for m in detail.requirement_matches:
            if m.gap_type in (GapType.INTERVIEW_PREP_GAP, GapType.RESUME_LANGUAGE_GAP):
                watchouts.append(f"{m.requirement.text}: {m.reason}")

    comp = result.fit.compensation_fit
    if comp.confidence < _COMP_CONFIDENCE_THRESHOLD:
        watchouts.append("Compensation is not published.")

    if decision.scope.primary_dimension in _LOW_SCOPE_DIMENSIONS:
        watchouts.append(
            f"Scope leans toward {decision.scope.primary_dimension.value} rather than "
            "architecture/ownership."
        )

    if decision.freshness.level == Freshness.STALE:
        watchouts.append(f"Posting is stale: {decision.freshness.reason}")

    return watchouts[:_MAX_WATCHOUTS]


def build_alert_content(
    company: str,
    role: str,
    location: str,
    remote_status: str,
    compensation: str,
    source: str,
    url: str,
    result: EvaluationResult,
    related_variant_count: int = 0,
) -> AlertContent:
    return AlertContent(
        company=company or "Unknown company",
        role=role,
        location=location or "Not specified",
        work_arrangement=remote_status,
        compensation=compensation,
        source=source,
        url=url,
        pursue=result.decision.pursue.recommendation.value,
        eligibility=result.decision.eligibility.status.value,
        qualification=result.decision.qualification.status.value,
        career_direction=result.direction.level.value,
        opportunity_value=result.immediate.level.value,
        reasons=_build_reasons(result),
        watchouts=_build_watchouts(result),
        related_variant_count=related_variant_count,
    )
