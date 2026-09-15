from careers_os.career.bridge_role import BridgeClassification, BridgeRoleResult
from careers_os.career.discovery_ranking import compute_rank_score
from careers_os.career.opportunity_value import OpportunityAssessment, OpportunityLevel
from careers_os.career.preferences import Preferences
from careers_os.domain.scoring import CareerFitResult, FitComponent


def _fit(overall_fit: float) -> CareerFitResult:
    comp = FitComponent(score=overall_fit, reason="r", confidence=0.8)
    return CareerFitResult(
        role_fit=comp, technical_fit=comp, career_direction_fit=comp,
        compensation_fit=comp, work_arrangement_fit=comp, experience_fit=comp,
        overall_fit=overall_fit, scorer_version="test",
    )


def _bridge(classification: BridgeClassification) -> BridgeRoleResult:
    return BridgeRoleResult(classification=classification, reason="r")


def _opportunity(level: OpportunityLevel) -> OpportunityAssessment:
    return OpportunityAssessment(level=level, reason="r")


class TestComputeRankScore:
    def test_ranking_is_not_a_pure_overall_fit_sort(self):
        weights = Preferences.load().discovery_ranking_weights

        # High overall_fit but weak on every discovery-specific dimension.
        high_fit_weak_everything = compute_rank_score(
            _fit(0.85), _bridge(BridgeClassification.WEAK),
            _opportunity(OpportunityLevel.WEAK), _opportunity(OpportunityLevel.WEAK),
            weights,
        )
        # Lower overall_fit but strong on every discovery-specific dimension.
        lower_fit_strong_everything = compute_rank_score(
            _fit(0.60), _bridge(BridgeClassification.STRONG),
            _opportunity(OpportunityLevel.STRONG), _opportunity(OpportunityLevel.STRONG),
            weights,
        )
        assert lower_fit_strong_everything > high_fit_weak_everything

    def test_strong_bridge_role_outranks_weak_bridge_role_at_equal_fit(self):
        weights = Preferences.load().discovery_ranking_weights
        strong = compute_rank_score(
            _fit(0.7), _bridge(BridgeClassification.STRONG),
            _opportunity(OpportunityLevel.MODERATE), _opportunity(OpportunityLevel.MODERATE),
            weights,
        )
        weak = compute_rank_score(
            _fit(0.7), _bridge(BridgeClassification.WEAK),
            _opportunity(OpportunityLevel.MODERATE), _opportunity(OpportunityLevel.MODERATE),
            weights,
        )
        assert strong > weak

    def test_unknown_bridge_is_treated_as_neutral_not_penalized_like_none(self):
        weights = Preferences.load().discovery_ranking_weights
        unknown = compute_rank_score(
            _fit(0.7), _bridge(BridgeClassification.UNKNOWN),
            _opportunity(OpportunityLevel.MODERATE), _opportunity(OpportunityLevel.MODERATE),
            weights,
        )
        none_ = compute_rank_score(
            _fit(0.7), _bridge(BridgeClassification.NONE),
            _opportunity(OpportunityLevel.MODERATE), _opportunity(OpportunityLevel.MODERATE),
            weights,
        )
        assert unknown > none_

    def test_missing_weights_falls_back_to_sane_default(self):
        score = compute_rank_score(
            _fit(0.7), _bridge(BridgeClassification.STRONG),
            _opportunity(OpportunityLevel.STRONG), _opportunity(OpportunityLevel.STRONG),
            weights=None,
        )
        assert 0.0 <= score <= 1.0

    def test_score_is_bounded_between_zero_and_one(self):
        weights = Preferences.load().discovery_ranking_weights
        best = compute_rank_score(
            _fit(1.0), _bridge(BridgeClassification.STRONG),
            _opportunity(OpportunityLevel.STRONG), _opportunity(OpportunityLevel.STRONG),
            weights,
        )
        worst = compute_rank_score(
            _fit(0.0), _bridge(BridgeClassification.NONE),
            _opportunity(OpportunityLevel.WEAK), _opportunity(OpportunityLevel.WEAK),
            weights,
        )
        assert 0.0 <= worst < best <= 1.0
