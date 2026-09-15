from careers_os.career.preferences import AlertModeSettings, AlertPolicy
from careers_os.domain.enums import OperatingMode
from careers_os.domain.eligibility import EligibilityResult, EligibilityStatus
from careers_os.domain.opportunity_decision import (
    Freshness,
    FreshnessResult,
    OpportunityCostLevel,
    OpportunityCostResult,
    OpportunityDecision,
    PursueRecommendation,
    PursueResult,
    ScopeResult,
)
from careers_os.domain.qualification import QualificationResult, QualificationStatus
from careers_os.notifications.policy import alert_rank, is_alert_eligible, should_alert

_POLICY = AlertPolicy(
    passive=AlertModeSettings(minimum_pursue=PursueRecommendation.STRONG_PURSUE),
    active=AlertModeSettings(minimum_pursue=PursueRecommendation.PURSUE),
)


def _decision(recommendation: PursueRecommendation) -> OpportunityDecision:
    return OpportunityDecision(
        eligibility=EligibilityResult(status=EligibilityStatus.ELIGIBLE),
        qualification=QualificationResult(status=QualificationStatus.STRONG, reason="test"),
        opportunity_cost=OpportunityCostResult(level=OpportunityCostLevel.LOW, reason="test"),
        scope=ScopeResult(reason="test"),
        freshness=FreshnessResult(level=Freshness.FRESH, reason="test"),
        pursue=PursueResult(recommendation=recommendation, reason="test"),
    )


class TestPassiveMode:
    def test_strong_pursue_meets_passive_threshold(self):
        assert should_alert(_decision(PursueRecommendation.STRONG_PURSUE), _POLICY, OperatingMode.PASSIVE)

    def test_plain_pursue_does_not_meet_passive_threshold(self):
        assert not should_alert(_decision(PursueRecommendation.PURSUE), _POLICY, OperatingMode.PASSIVE)

    def test_consider_does_not_alert_in_passive_mode(self):
        assert not should_alert(_decision(PursueRecommendation.CONSIDER), _POLICY, OperatingMode.PASSIVE)


class TestActiveMode:
    def test_pursue_meets_active_threshold(self):
        assert should_alert(_decision(PursueRecommendation.PURSUE), _POLICY, OperatingMode.ACTIVE)

    def test_strong_pursue_also_meets_active_threshold(self):
        assert should_alert(_decision(PursueRecommendation.STRONG_PURSUE), _POLICY, OperatingMode.ACTIVE)

    def test_consider_does_not_meet_active_threshold(self):
        assert not should_alert(_decision(PursueRecommendation.CONSIDER), _POLICY, OperatingMode.ACTIVE)


class TestHardFloor:
    def test_verify_first_never_alerts_even_in_active_mode(self):
        assert not is_alert_eligible(_decision(PursueRecommendation.VERIFY_FIRST))
        assert not should_alert(_decision(PursueRecommendation.VERIFY_FIRST), _POLICY, OperatingMode.ACTIVE)

    def test_do_not_pursue_never_alerts(self):
        assert not is_alert_eligible(_decision(PursueRecommendation.DO_NOT_PURSUE))
        assert not should_alert(_decision(PursueRecommendation.DO_NOT_PURSUE), _POLICY, OperatingMode.ACTIVE)

    def test_eligibility_alone_is_not_sufficient(self):
        # A merely-eligible, low-priority job must not alert regardless of mode.
        assert not should_alert(_decision(PursueRecommendation.LOW_PRIORITY), _POLICY, OperatingMode.ACTIVE)


class TestAlertRank:
    def test_rank_increases_with_recommendation_strength(self):
        assert alert_rank(PursueRecommendation.STRONG_PURSUE) > alert_rank(PursueRecommendation.PURSUE)
        assert alert_rank(PursueRecommendation.PURSUE) > alert_rank(PursueRecommendation.CONSIDER)
        assert alert_rank(PursueRecommendation.CONSIDER) > alert_rank(PursueRecommendation.LOW_PRIORITY)

    def test_non_alertable_recommendations_rank_zero(self):
        assert alert_rank(PursueRecommendation.VERIFY_FIRST) == 0
        assert alert_rank(PursueRecommendation.DO_NOT_PURSUE) == 0
