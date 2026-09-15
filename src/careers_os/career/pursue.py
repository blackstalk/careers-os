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
    OpportunityCostLevel,
    OpportunityCostResult,
    PursueRecommendation,
    PursueResult,
)
from careers_os.domain.qualification import QualificationResult, QualificationStatus


def compute_pursue_recommendation(
    eligibility: EligibilityResult,
    qualification: QualificationResult,
    immediate: OpportunityAssessment,
    direction: OpportunityAssessment,
    opportunity_cost: OpportunityCostResult,
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

    if opportunity_cost.level == OpportunityCostLevel.HIGH:
        return PursueResult(
            recommendation=PursueRecommendation.LOW_PRIORITY,
            reason=opportunity_cost.reason,
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
