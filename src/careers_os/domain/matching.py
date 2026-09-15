from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from careers_os.domain.requirements import JobRequirement


class MatchType(str, Enum):
    STRONG_MATCH = "strong_match"
    PARTIAL_MATCH = "partial_match"
    ADJACENT_EXPERIENCE = "adjacent_experience"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class GapType(str, Enum):
    REAL_EXPERIENCE_GAP = "real_experience_gap"
    RESUME_LANGUAGE_GAP = "resume_language_gap"
    INTERVIEW_PREP_GAP = "interview_prep_gap"
    UNKNOWN = "unknown"


# Deterministic 1:1 mapping from match outcome to gap classification. This
# is a starting heuristic, not a semantic classifier — see
# docs/evidence-model.md "Gap classification is a heuristic, not a verdict"
# for the reasoning and its limits.
MATCH_TYPE_TO_GAP_TYPE: dict[MatchType, Optional[GapType]] = {
    MatchType.STRONG_MATCH: None,
    MatchType.PARTIAL_MATCH: GapType.RESUME_LANGUAGE_GAP,
    MatchType.ADJACENT_EXPERIENCE: GapType.INTERVIEW_PREP_GAP,
    MatchType.UNSUPPORTED: GapType.REAL_EXPERIENCE_GAP,
    MatchType.UNKNOWN: GapType.UNKNOWN,
}


class EvidenceRef(BaseModel):
    """Lightweight citation — a RequirementMatch can point at real evidence
    without embedding the full Evidence object."""

    evidence_id: str
    statement: str
    company: Optional[str] = None
    variant_slug: Optional[str] = None  # which resume variant this evidence came from


class RequirementMatch(BaseModel):
    requirement: JobRequirement
    match_type: MatchType
    matched_evidence: list[EvidenceRef] = Field(default_factory=list)
    reason: str
    gap_type: Optional[GapType] = None
