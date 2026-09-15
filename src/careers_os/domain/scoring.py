from typing import Optional

from pydantic import BaseModel, Field

from careers_os.domain.experience import ExperienceFitDetail


class FitComponent(BaseModel):
    """One explainable component of a job's career fit.

    `confidence` is honesty about how much the score should be trusted, not
    a second score — a component built from a missing field (e.g. no salary
    published) should report a low confidence and say so in `reason`,
    never invent a number to fill the gap.
    """

    score: float = Field(ge=0.0, le=1.0)
    reason: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class CareerFitResult(BaseModel):
    role_fit: FitComponent
    technical_fit: FitComponent
    career_direction_fit: FitComponent
    compensation_fit: FitComponent
    work_arrangement_fit: FitComponent
    experience_fit: FitComponent

    overall_fit: float = Field(ge=0.0, le=1.0)

    scorer_version: str
    ai_evaluation_included: bool = False

    # Full explainable breakdown behind `experience_fit` — per-requirement
    # matches, gaps, and years-of-experience estimates. None when no resume
    # has been imported yet (experience_fit falls back to a neutral
    # placeholder in that case — see scoring/evidence_matcher.py).
    experience_detail: Optional[ExperienceFitDetail] = None

    def components(self) -> dict[str, FitComponent]:
        return {
            "role_fit": self.role_fit,
            "technical_fit": self.technical_fit,
            "career_direction_fit": self.career_direction_fit,
            "compensation_fit": self.compensation_fit,
            "work_arrangement_fit": self.work_arrangement_fit,
            "experience_fit": self.experience_fit,
        }
