from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from careers_os.domain.requirements import JobRequirement

AIConfidence = Literal["low", "moderate", "high"]


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


class AIAssessmentType(str, Enum):
    """The complete vocabulary an AI evidence assessment is allowed to use.

    Deliberately excludes anything resembling "direct"/"strong" evidence —
    the AI reasoner (scoring/evidence_reasoner.py) is structurally
    incapable of upgrading a deterministic gap into a claim of direct
    experience, because that value doesn't exist in this enum. See
    docs/eligibility.md#ai-evidence-guardrails.
    """

    TRANSFERABLE_CAPABILITY = "transferable_capability"
    RESUME_LANGUAGE_GAP = "resume_language_gap"
    INTERVIEW_PREP_GAP = "interview_prep_gap"
    NO_CHANGE = "no_change"  # AI reviewed it and agrees the deterministic gap is real


class AIEvidenceAssessment(BaseModel):
    """An AI reasoner's annotation on top of a deterministic RequirementMatch.

    Never replaces `match_type`/`gap_type` — those stay purely
    deterministic. `evidence_ids` must be non-empty and must reference
    real `Evidence.id`s already in the candidate's evidence store; both
    are enforced by scoring/evidence_reasoner.py before this is ever
    attached to a RequirementMatch, not just documented here.
    """

    model_config = {"extra": "forbid"}

    requirement_id: str
    assessment_type: AIAssessmentType
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str
    confidence: AIConfidence


class RequirementMatch(BaseModel):
    requirement: JobRequirement
    match_type: MatchType
    matched_evidence: list[EvidenceRef] = Field(default_factory=list)
    reason: str
    gap_type: Optional[GapType] = None
    ai_assessment: Optional[AIEvidenceAssessment] = None
