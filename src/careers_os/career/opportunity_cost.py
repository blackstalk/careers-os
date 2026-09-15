"""Opportunity-cost classification (Phase 3).

Not a life-modeling exercise — see docs/pursue-recommendation.md. The
purpose is narrow: flag roles where qualification is high but the actual
value of spending time on them is low (pay below target, career direction
weak), so a "you'd definitely get the job" role doesn't automatically
outrank a role that's actually worth pursuing.
"""

from careers_os.career.opportunity_value import OpportunityAssessment, OpportunityLevel
from careers_os.domain.opportunity_decision import OpportunityCostLevel, OpportunityCostResult
from careers_os.domain.qualification import QualificationResult, QualificationStatus
from careers_os.domain.scoring import FitComponent

_COMP_CONFIDENCE_THRESHOLD = 0.3
_COMP_BELOW_TARGET_SCORE = 0.5
_COMP_STRONG_SCORE = 0.75


def assess_opportunity_cost(
    qualification: QualificationResult,
    compensation_fit: FitComponent,
    direction: OpportunityAssessment,
) -> OpportunityCostResult:
    comp_known = compensation_fit.confidence >= _COMP_CONFIDENCE_THRESHOLD
    comp_below_target = comp_known and compensation_fit.score < _COMP_BELOW_TARGET_SCORE
    comp_strong = comp_known and compensation_fit.score >= _COMP_STRONG_SCORE
    direction_weak = direction.level == OpportunityLevel.WEAK
    direction_strong = direction.level == OpportunityLevel.STRONG
    highly_qualified = qualification.status in (QualificationStatus.STRONG, QualificationStatus.MODERATE)

    if not comp_known and direction.level == OpportunityLevel.UNKNOWN:
        return OpportunityCostResult(
            level=OpportunityCostLevel.UNKNOWN,
            reason="Not enough compensation or career-direction signal to assess opportunity cost.",
        )

    if highly_qualified and comp_below_target and direction_weak:
        return OpportunityCostResult(
            level=OpportunityCostLevel.HIGH,
            reason="Strong/moderate qualification fit, but compensation is below target and career "
            "direction is weak — time spent here has a real opportunity cost relative to the "
            "target direction.",
        )

    if comp_strong or direction_strong:
        return OpportunityCostResult(
            level=OpportunityCostLevel.LOW,
            reason="Compensation and/or career direction are strong enough that time here is well spent.",
        )

    return OpportunityCostResult(
        level=OpportunityCostLevel.MODERATE,
        reason="Mixed signal on compensation and career direction — neither clearly high nor low cost.",
    )
