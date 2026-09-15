from pydantic import BaseModel, Field

from careers_os.domain.matching import GapType, MatchType, RequirementMatch


class YearsEstimate(BaseModel):
    """Reusable years-of-experience estimate for one skill/theme.

    Deliberately imprecise on display (`display_years`, e.g. "6+ years")
    rather than false-precision like "6.42 years" — see
    docs/evidence-model.md "Avoid false precision".
    """

    skill: str  # canonical skill key, or "overall" for total career span
    display_years: str
    numeric_years: float
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_companies: list[str] = Field(default_factory=list)


class ExperienceFitDetail(BaseModel):
    """The full explainable breakdown behind one job's experience_fit score.
    This is what replaces the Phase 1 placeholder — see
    docs/scoring.md#experience-fit.
    """

    requirement_matches: list[RequirementMatch] = Field(default_factory=list)
    years_estimates: dict[str, YearsEstimate] = Field(default_factory=dict)
    resume_available: bool = True  # False when no resume has been imported yet

    @property
    def strong_matches(self) -> list[RequirementMatch]:
        return [m for m in self.requirement_matches if m.match_type == MatchType.STRONG_MATCH]

    @property
    def partial_matches(self) -> list[RequirementMatch]:
        return [
            m
            for m in self.requirement_matches
            if m.match_type in (MatchType.PARTIAL_MATCH, MatchType.ADJACENT_EXPERIENCE)
        ]

    @property
    def gaps(self) -> list[RequirementMatch]:
        return [
            m
            for m in self.requirement_matches
            if m.gap_type is not None and m.gap_type != GapType.UNKNOWN
        ]

    @property
    def real_experience_gaps(self) -> list[RequirementMatch]:
        return [m for m in self.requirement_matches if m.gap_type == GapType.REAL_EXPERIENCE_GAP]
