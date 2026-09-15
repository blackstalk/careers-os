from careers_os.career.opportunity_value import OpportunityAssessment, OpportunityLevel
from careers_os.career.pursue import compute_pursue_recommendation
from careers_os.domain.eligibility import EligibilityCheck, EligibilityResult, EligibilityStatus, ConstraintType
from careers_os.domain.opportunity_decision import OpportunityCostLevel, OpportunityCostResult, PursueRecommendation
from careers_os.domain.qualification import QualificationResult, QualificationStatus


def _eligibility(status: EligibilityStatus) -> EligibilityResult:
    checks = []
    if status != EligibilityStatus.ELIGIBLE:
        checks = [EligibilityCheck(
            requirement="test constraint", constraint_type=ConstraintType.TIMEZONE,
            candidate_evidence="x", status=status, confidence=0.5, reason="test",
        )]
    return EligibilityResult(status=status, checks=checks)


def _qual(status: QualificationStatus) -> QualificationResult:
    return QualificationResult(status=status, reason="test")


def _level(level: OpportunityLevel) -> OpportunityAssessment:
    return OpportunityAssessment(level=level, reason="test")


def _cost(level: OpportunityCostLevel) -> OpportunityCostResult:
    return OpportunityCostResult(level=level, reason="test")


class TestHardGatePrecedence:
    def test_ineligible_always_wins_regardless_of_fit(self):
        result = compute_pursue_recommendation(
            _eligibility(EligibilityStatus.INELIGIBLE),
            _qual(QualificationStatus.STRONG),
            _level(OpportunityLevel.STRONG), _level(OpportunityLevel.STRONG),
            _cost(OpportunityCostLevel.LOW),
        )
        assert result.recommendation == PursueRecommendation.DO_NOT_PURSUE

    def test_qualification_fail_always_wins_regardless_of_direction(self):
        # Mirrors the real Stripe Go Backend Engineer case: strong direction
        # signal must not rescue a failed hard qualification gate.
        result = compute_pursue_recommendation(
            _eligibility(EligibilityStatus.ELIGIBLE),
            _qual(QualificationStatus.FAIL),
            _level(OpportunityLevel.STRONG), _level(OpportunityLevel.STRONG),
            _cost(OpportunityCostLevel.LOW),
        )
        assert result.recommendation == PursueRecommendation.DO_NOT_PURSUE

    def test_verify_eligibility_wins_over_otherwise_excellent_fit(self):
        # Mirrors the real Stripe Technical Solutions Engineer case.
        result = compute_pursue_recommendation(
            _eligibility(EligibilityStatus.VERIFY),
            _qual(QualificationStatus.STRONG),
            _level(OpportunityLevel.STRONG), _level(OpportunityLevel.STRONG),
            _cost(OpportunityCostLevel.LOW),
        )
        assert result.recommendation == PursueRecommendation.VERIFY_FIRST

    def test_unknown_eligibility_also_yields_verify_first(self):
        result = compute_pursue_recommendation(
            _eligibility(EligibilityStatus.UNKNOWN),
            _qual(QualificationStatus.STRONG),
            _level(OpportunityLevel.STRONG), _level(OpportunityLevel.STRONG),
            _cost(OpportunityCostLevel.LOW),
        )
        assert result.recommendation == PursueRecommendation.VERIFY_FIRST

    def test_high_opportunity_cost_forces_low_priority_even_when_qualified(self):
        # Mirrors the real Creative Circle PHP case.
        result = compute_pursue_recommendation(
            _eligibility(EligibilityStatus.ELIGIBLE),
            _qual(QualificationStatus.STRONG),
            _level(OpportunityLevel.MODERATE), _level(OpportunityLevel.WEAK),
            _cost(OpportunityCostLevel.HIGH),
        )
        assert result.recommendation == PursueRecommendation.LOW_PRIORITY


class TestPositiveRecommendations:
    def test_strong_everything_is_strong_pursue(self):
        result = compute_pursue_recommendation(
            _eligibility(EligibilityStatus.ELIGIBLE),
            _qual(QualificationStatus.STRONG),
            _level(OpportunityLevel.STRONG), _level(OpportunityLevel.STRONG),
            _cost(OpportunityCostLevel.LOW),
        )
        assert result.recommendation == PursueRecommendation.STRONG_PURSUE

    def test_moderate_fit_strong_bridge_direction_can_pursue(self):
        result = compute_pursue_recommendation(
            _eligibility(EligibilityStatus.ELIGIBLE),
            _qual(QualificationStatus.MODERATE),
            _level(OpportunityLevel.MODERATE), _level(OpportunityLevel.STRONG),
            _cost(OpportunityCostLevel.MODERATE),
        )
        assert result.recommendation == PursueRecommendation.PURSUE

    def test_weak_qualification_and_no_direction_signal_is_low_priority(self):
        result = compute_pursue_recommendation(
            _eligibility(EligibilityStatus.ELIGIBLE),
            _qual(QualificationStatus.WEAK),
            _level(OpportunityLevel.WEAK), _level(OpportunityLevel.WEAK),
            _cost(OpportunityCostLevel.MODERATE),
        )
        assert result.recommendation == PursueRecommendation.LOW_PRIORITY

    def test_eligible_low_fit_job_is_never_strong_pursue(self):
        result = compute_pursue_recommendation(
            _eligibility(EligibilityStatus.ELIGIBLE),
            _qual(QualificationStatus.WEAK),
            _level(OpportunityLevel.WEAK), _level(OpportunityLevel.WEAK),
            _cost(OpportunityCostLevel.LOW),
        )
        assert result.recommendation != PursueRecommendation.STRONG_PURSUE

    def test_mixed_signal_is_consider(self):
        result = compute_pursue_recommendation(
            _eligibility(EligibilityStatus.ELIGIBLE),
            _qual(QualificationStatus.MODERATE),
            _level(OpportunityLevel.MODERATE), _level(OpportunityLevel.MODERATE),
            _cost(OpportunityCostLevel.MODERATE),
        )
        assert result.recommendation == PursueRecommendation.CONSIDER


class TestExplainability:
    def test_reason_and_contributing_factors_are_populated(self):
        result = compute_pursue_recommendation(
            _eligibility(EligibilityStatus.ELIGIBLE),
            _qual(QualificationStatus.STRONG),
            _level(OpportunityLevel.STRONG), _level(OpportunityLevel.STRONG),
            _cost(OpportunityCostLevel.LOW),
        )
        assert result.reason
        assert result.contributing_factors
