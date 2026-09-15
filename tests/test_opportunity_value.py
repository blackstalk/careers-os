from careers_os.career.opportunity_value import (
    OpportunityLevel,
    assess_career_direction,
    assess_immediate_opportunity,
)
from careers_os.domain.scoring import CareerFitResult, FitComponent


def _fit(**overrides) -> CareerFitResult:
    defaults = dict(
        role_fit=FitComponent(score=0.5, reason="r", confidence=0.5),
        technical_fit=FitComponent(score=0.5, reason="r", confidence=0.5),
        career_direction_fit=FitComponent(score=0.5, reason="r", confidence=0.5),
        compensation_fit=FitComponent(score=0.5, reason="r", confidence=0.5),
        work_arrangement_fit=FitComponent(score=0.5, reason="r", confidence=0.5),
        experience_fit=FitComponent(score=0.5, reason="r", confidence=0.5),
        overall_fit=0.5,
        scorer_version="test",
    )
    defaults.update(overrides)
    return CareerFitResult(**defaults)


class TestImmediateOpportunity:
    def test_high_pay_high_experience_fit_is_strong(self):
        fit = _fit(
            experience_fit=FitComponent(score=0.9, reason="r", confidence=0.8),
            technical_fit=FitComponent(score=0.9, reason="r", confidence=0.8),
            compensation_fit=FitComponent(score=0.9, reason="r", confidence=0.9),
            work_arrangement_fit=FitComponent(score=0.9, reason="r", confidence=0.9),
        )
        assessment = assess_immediate_opportunity(fit)
        assert assessment.level == OpportunityLevel.STRONG

    def test_missing_compensation_does_not_aggressively_penalize(self):
        # Missing comp scores neutral (0.5) with low confidence in the
        # underlying compensation_fit scorer — that neutral 0.5 must not
        # drag a role with otherwise strong signal down to "weak".
        fit = _fit(
            experience_fit=FitComponent(score=0.9, reason="r", confidence=0.85),
            technical_fit=FitComponent(score=0.9, reason="r", confidence=0.8),
            compensation_fit=FitComponent(score=0.5, reason="unknown", confidence=0.1),
            work_arrangement_fit=FitComponent(score=0.9, reason="r", confidence=0.9),
        )
        assessment = assess_immediate_opportunity(fit)
        assert assessment.level in (OpportunityLevel.STRONG, OpportunityLevel.MODERATE)
        assert "compensation unknown" in assessment.reason

    def test_low_signal_everywhere_is_weak_not_unknown(self):
        fit = _fit(
            experience_fit=FitComponent(score=0.1, reason="r", confidence=0.5),
            technical_fit=FitComponent(score=0.1, reason="r", confidence=0.5),
            compensation_fit=FitComponent(score=0.1, reason="r", confidence=0.5),
            work_arrangement_fit=FitComponent(score=0.1, reason="r", confidence=0.5),
        )
        assessment = assess_immediate_opportunity(fit)
        assert assessment.level == OpportunityLevel.WEAK

    def test_no_reliable_signal_at_all_is_unknown(self):
        fit = _fit(
            experience_fit=FitComponent(score=0.5, reason="r", confidence=0.05),
            technical_fit=FitComponent(score=0.5, reason="r", confidence=0.05),
            compensation_fit=FitComponent(score=0.5, reason="r", confidence=0.05),
            work_arrangement_fit=FitComponent(score=0.5, reason="r", confidence=0.05),
        )
        assessment = assess_immediate_opportunity(fit)
        assert assessment.level == OpportunityLevel.UNKNOWN


class TestCareerDirection:
    def test_strong_career_direction_fit_yields_strong(self):
        fit = _fit(
            career_direction_fit=FitComponent(score=0.9, reason="architecture-heavy", confidence=0.8),
            role_fit=FitComponent(score=0.9, reason="r", confidence=0.8),
        )
        assessment = assess_career_direction(fit)
        assert assessment.level == OpportunityLevel.STRONG

    def test_weak_career_direction_fit_yields_weak(self):
        fit = _fit(
            career_direction_fit=FitComponent(score=0.1, reason="commodity work", confidence=0.7),
            role_fit=FitComponent(score=0.1, reason="r", confidence=0.7),
        )
        assessment = assess_career_direction(fit)
        assert assessment.level == OpportunityLevel.WEAK

    def test_immediate_opportunity_and_career_direction_are_independent(self):
        # A role can score high on immediate opportunity and low on career
        # direction (or vice versa) — the two must not be coupled.
        fit = _fit(
            experience_fit=FitComponent(score=0.95, reason="r", confidence=0.9),
            technical_fit=FitComponent(score=0.95, reason="r", confidence=0.9),
            compensation_fit=FitComponent(score=0.95, reason="r", confidence=0.9),
            work_arrangement_fit=FitComponent(score=0.95, reason="r", confidence=0.9),
            career_direction_fit=FitComponent(score=0.05, reason="r", confidence=0.9),
            role_fit=FitComponent(score=0.05, reason="r", confidence=0.9),
        )
        immediate = assess_immediate_opportunity(fit)
        direction = assess_career_direction(fit)
        assert immediate.level == OpportunityLevel.STRONG
        assert direction.level == OpportunityLevel.WEAK
