from careers_os.career.opportunity_cost import assess_opportunity_cost
from careers_os.career.opportunity_value import OpportunityAssessment, OpportunityLevel
from careers_os.domain.opportunity_decision import OpportunityCostLevel
from careers_os.domain.qualification import QualificationResult, QualificationStatus
from careers_os.domain.scoring import FitComponent


def _qual(status: QualificationStatus) -> QualificationResult:
    return QualificationResult(status=status, reason="test")


def _comp(score: float, confidence: float) -> FitComponent:
    return FitComponent(score=score, reason="test", confidence=confidence)


def _direction(level: OpportunityLevel) -> OpportunityAssessment:
    return OpportunityAssessment(level=level, reason="test")


class TestHighOpportunityCost:
    def test_strong_qualification_low_pay_weak_direction_is_high_cost(self):
        # Mirrors the real Creative Circle PHP case.
        result = assess_opportunity_cost(
            _qual(QualificationStatus.STRONG), _comp(0.44, 0.85), _direction(OpportunityLevel.WEAK)
        )
        assert result.level == OpportunityCostLevel.HIGH


class TestLowOpportunityCost:
    def test_strong_compensation_is_low_cost_even_with_weak_direction(self):
        result = assess_opportunity_cost(
            _qual(QualificationStatus.STRONG), _comp(0.9, 0.9), _direction(OpportunityLevel.WEAK)
        )
        assert result.level == OpportunityCostLevel.LOW

    def test_strong_direction_is_low_cost_even_with_below_target_pay(self):
        result = assess_opportunity_cost(
            _qual(QualificationStatus.MODERATE), _comp(0.3, 0.9), _direction(OpportunityLevel.STRONG)
        )
        assert result.level == OpportunityCostLevel.LOW


class TestUnknownOpportunityCost:
    def test_no_signal_at_all_is_unknown(self):
        result = assess_opportunity_cost(
            _qual(QualificationStatus.UNKNOWN), _comp(0.5, 0.05), _direction(OpportunityLevel.UNKNOWN)
        )
        assert result.level == OpportunityCostLevel.UNKNOWN

    def test_missing_compensation_does_not_force_high_cost(self):
        result = assess_opportunity_cost(
            _qual(QualificationStatus.STRONG), _comp(0.5, 0.05), _direction(OpportunityLevel.MODERATE)
        )
        assert result.level != OpportunityCostLevel.HIGH
