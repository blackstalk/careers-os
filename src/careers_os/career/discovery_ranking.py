"""Discovery ranking (Phase 2.5).

Deliberately not a sort by `overall_fit` alone — see docs/discovery.md.
Combines overall_fit with bridge-role strength, immediate-opportunity
value, and career-direction alignment into one sortable number, using
configurable weights (`career/data/preferences.yaml`'s
`discovery_ranking_weights`). Every input dimension stays independently
visible in `jobs discover` output; this function only decides sort order.
"""

from careers_os.career.bridge_role import BridgeClassification, BridgeRoleResult
from careers_os.career.opportunity_value import OpportunityAssessment, OpportunityLevel
from careers_os.career.preferences import DiscoveryRankingWeights
from careers_os.domain.scoring import CareerFitResult

_DEFAULT_WEIGHTS = DiscoveryRankingWeights(
    overall_fit=0.40, bridge_role=0.20, immediate_opportunity=0.25, career_direction=0.15
)

_BRIDGE_SCORE = {
    BridgeClassification.STRONG: 1.0,
    BridgeClassification.MODERATE: 0.6,
    BridgeClassification.WEAK: 0.3,
    BridgeClassification.NONE: 0.0,
    BridgeClassification.UNKNOWN: 0.4,  # neutral-ish — not enough text, not a penalty
}

_OPPORTUNITY_SCORE = {
    OpportunityLevel.STRONG: 1.0,
    OpportunityLevel.MODERATE: 0.6,
    OpportunityLevel.WEAK: 0.25,
    OpportunityLevel.UNKNOWN: 0.5,  # neutral — unknown is not the same as weak
}


def compute_rank_score(
    fit: CareerFitResult,
    bridge: BridgeRoleResult,
    immediate: OpportunityAssessment,
    direction: OpportunityAssessment,
    weights: DiscoveryRankingWeights | None = None,
) -> float:
    weights = weights or _DEFAULT_WEIGHTS
    total_weight = sum(weights.as_dict().values()) or 1.0

    weighted = (
        fit.overall_fit * weights.overall_fit
        + _BRIDGE_SCORE[bridge.classification] * weights.bridge_role
        + _OPPORTUNITY_SCORE[immediate.level] * weights.immediate_opportunity
        + _OPPORTUNITY_SCORE[direction.level] * weights.career_direction
    )
    return round(weighted / total_weight, 4)
