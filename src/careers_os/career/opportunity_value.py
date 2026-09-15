"""Immediate-opportunity and career-direction classification (Phase 2.5).

Both are deliberately categorical (Strong/Moderate/Weak/Unknown), not a
second opaque number — see docs/discovery.md#ranking-philosophy. They're
thin, explainable views over `CareerFitResult` components that already
exist; nothing here recomputes fit from scratch.
"""

from enum import Enum

from pydantic import BaseModel

from careers_os.domain.scoring import CareerFitResult

_LOW_CONFIDENCE_THRESHOLD = 0.15


class OpportunityLevel(str, Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    UNKNOWN = "unknown"


class OpportunityAssessment(BaseModel):
    level: OpportunityLevel
    reason: str


def _bucket(score: float) -> OpportunityLevel:
    if score >= 0.75:
        return OpportunityLevel.STRONG
    if score >= 0.5:
        return OpportunityLevel.MODERATE
    return OpportunityLevel.WEAK


def assess_immediate_opportunity(fit: CareerFitResult) -> OpportunityAssessment:
    """"Can I credibly win this today?" — experience, technical fit,
    compensation, and work arrangement. Deliberately excludes role_fit and
    career_direction_fit, which measure something different (see
    assess_career_direction below) — a role can be an excellent immediate
    opportunity while pointing nowhere career-wise, and vice versa.
    """
    components = [fit.experience_fit, fit.technical_fit, fit.compensation_fit, fit.work_arrangement_fit]
    if all(c.confidence < _LOW_CONFIDENCE_THRESHOLD for c in components):
        return OpportunityAssessment(
            level=OpportunityLevel.UNKNOWN,
            reason="Not enough reliable signal (experience, technical fit, compensation, "
            "work arrangement) to assess immediate opportunity value.",
        )

    score = sum(c.score for c in components) / len(components)
    level = _bucket(score)

    parts = []
    if fit.compensation_fit.confidence < _LOW_CONFIDENCE_THRESHOLD:
        parts.append("compensation unknown")
    else:
        parts.append(f"compensation {_bucket(fit.compensation_fit.score).value}")
    parts.append(f"experience {_bucket(fit.experience_fit.score).value}")
    parts.append(f"technical {_bucket(fit.technical_fit.score).value}")
    parts.append(f"work arrangement {_bucket(fit.work_arrangement_fit.score).value}")

    return OpportunityAssessment(level=level, reason=", ".join(parts))


def assess_career_direction(fit: CareerFitResult) -> OpportunityAssessment:
    """"Does this move my career where I want it to go?" — career_direction_fit
    and role_fit only, deliberately separate from immediate-opportunity value.
    """
    score = (fit.career_direction_fit.score + fit.role_fit.score) / 2
    if fit.career_direction_fit.confidence < _LOW_CONFIDENCE_THRESHOLD and fit.role_fit.confidence < _LOW_CONFIDENCE_THRESHOLD:
        return OpportunityAssessment(
            level=OpportunityLevel.UNKNOWN,
            reason="Not enough description text to judge career-direction alignment.",
        )
    level = _bucket(score)
    return OpportunityAssessment(level=level, reason=fit.career_direction_fit.reason)
