"""Pursue recommendation (Phase 3) — the final decision layer.

Hard gates dominate everything else, deliberately in this order:
ineligible beats any fit; a failed hard qualification beats any fit;
"verify" eligibility beats a strong recommendation being made prematurely.
Only once none of those apply does the recommendation become a genuine
weighing of qualification, career direction, immediate opportunity, and
opportunity cost — see docs/pursue-recommendation.md.
"""

from careers_os.career.opportunity_value import OpportunityAssessment, OpportunityLevel
from careers_os.domain.eligibility import EligibilityResult, EligibilityStatus
from careers_os.domain.opportunity_decision import (
    CareerTrackResult,
    OpportunityCostLevel,
    OpportunityCostResult,
    PursueRecommendation,
    PursueResult,
    WorkStyle,
    TrackAlignment,
    WorkStyleResult,
)
from careers_os.domain.qualification import QualificationResult, QualificationStatus


def compute_pursue_recommendation(
    eligibility: EligibilityResult,
    qualification: QualificationResult,
    immediate: OpportunityAssessment,
    direction: OpportunityAssessment,
    opportunity_cost: OpportunityCostResult,
    work_style: WorkStyleResult | None = None,
    disfavored_work_styles: frozenset[WorkStyle] | set[WorkStyle] = frozenset(),
    career_track: CareerTrackResult | None = None,
) -> PursueResult:
    result = _base_recommendation(
        eligibility, qualification, immediate, direction, opportunity_cost, work_style, disfavored_work_styles
    )
    if career_track is None or result.recommendation not in _TRACK_CAPPED:
        return result
    factors = [*result.contributing_factors, f"career_track={career_track.alignment.value}"]

    # Career-track relevance (Phase 4.3): strong_pursue needs affirmative
    # evidence that the job is on one of the candidate's two paths; a
    # title that defines an unrelated role caps it at consider.
    if career_track.alignment == TrackAlignment.OFF_TRACK:
        return PursueResult(
            recommendation=PursueRecommendation.CONSIDER,
            reason=f"Capped at consider: {career_track.reason}",
            contributing_factors=factors,
        )
    if career_track.alignment == TrackAlignment.UNCLEAR and result.recommendation == PursueRecommendation.STRONG_PURSUE:
        return PursueResult(
            recommendation=PursueRecommendation.PURSUE,
            reason=f"{result.reason} Not strong: {career_track.reason}",
            contributing_factors=factors,
        )
    return result.model_copy(update={"contributing_factors": factors})


# Only these are adjusted by the career-track check; gates and lower
# recommendations already say "don't prioritize this".
_TRACK_CAPPED = frozenset({PursueRecommendation.STRONG_PURSUE, PursueRecommendation.PURSUE})


def _base_recommendation(
    eligibility: EligibilityResult,
    qualification: QualificationResult,
    immediate: OpportunityAssessment,
    direction: OpportunityAssessment,
    opportunity_cost: OpportunityCostResult,
    work_style: WorkStyleResult | None,
    disfavored_work_styles: frozenset[WorkStyle] | set[WorkStyle],
) -> PursueResult:
    factors: list[str] = []

    if eligibility.status == EligibilityStatus.INELIGIBLE:
        blocking = "; ".join(c.requirement for c in eligibility.blocking_checks)
        return PursueResult(
            recommendation=PursueRecommendation.DO_NOT_PURSUE,
            reason=f"Ineligible: {blocking}.",
            contributing_factors=[f"eligibility={eligibility.status.value}"],
        )

    if qualification.status == QualificationStatus.FAIL:
        gaps = "; ".join(m.requirement.text for m in qualification.hard_gaps)
        return PursueResult(
            recommendation=PursueRecommendation.DO_NOT_PURSUE,
            reason=f"Hard qualification gap with no supporting evidence: {gaps}.",
            contributing_factors=[f"qualification={qualification.status.value}"],
        )

    if eligibility.status in (EligibilityStatus.VERIFY, EligibilityStatus.UNKNOWN):
        checks = "; ".join(c.requirement for c in (eligibility.verify_checks + eligibility.unknown_checks))
        return PursueResult(
            recommendation=PursueRecommendation.VERIFY_FIRST,
            reason=f"Eligibility depends on information that needs to be confirmed: {checks}.",
            contributing_factors=[f"eligibility={eligibility.status.value}"],
        )

    factors.append(f"qualification={qualification.status.value}")
    factors.append(f"career_direction={direction.level.value}")
    factors.append(f"immediate_opportunity={immediate.level.value}")
    factors.append(f"opportunity_cost={opportunity_cost.level.value}")
    if work_style is not None:
        factors.append(f"work_style={work_style.style.value}")

    if opportunity_cost.level == OpportunityCostLevel.HIGH:
        return PursueResult(
            recommendation=PursueRecommendation.LOW_PRIORITY,
            reason=opportunity_cost.reason,
            contributing_factors=factors,
        )

    # Work-style fit (Phase 4.2): a disfavored day-to-day shape caps the
    # recommendation below pursue no matter how strong the technical match,
    # compensation, or title — but it is a downgrade, not a rejection.
    if work_style is not None and work_style.style in disfavored_work_styles:
        return PursueResult(
            recommendation=PursueRecommendation.CONSIDER,
            reason=(
                f"Technically relevant, but the role's day-to-day shape is {work_style.style.value.replace('_', ' ')}, "
                "which the candidate is moving away from."
            ),
            contributing_factors=factors,
        )

    highly_qualified = qualification.status in (QualificationStatus.STRONG, QualificationStatus.MODERATE)
    weakly_qualified = qualification.status in (QualificationStatus.WEAK, QualificationStatus.UNKNOWN)

    if highly_qualified and direction.level == OpportunityLevel.STRONG and immediate.level == OpportunityLevel.STRONG:
        return PursueResult(
            recommendation=PursueRecommendation.STRONG_PURSUE,
            reason="Strong qualification, strong immediate opportunity, and strong career-direction alignment.",
            contributing_factors=factors,
        )

    if highly_qualified and (direction.level == OpportunityLevel.STRONG or immediate.level == OpportunityLevel.STRONG):
        return PursueResult(
            recommendation=PursueRecommendation.PURSUE,
            reason="Solid qualification with a strong signal on either career direction or immediate opportunity.",
            contributing_factors=factors,
        )

    if weakly_qualified and direction.level != OpportunityLevel.STRONG:
        return PursueResult(
            recommendation=PursueRecommendation.LOW_PRIORITY,
            reason="Weak/unclear qualification and no strong career-direction signal to offset it.",
            contributing_factors=factors,
        )

    return PursueResult(
        recommendation=PursueRecommendation.CONSIDER,
        reason="Mixed signal — worth a closer look, not a clear pursue or pass.",
        contributing_factors=factors,
    )
