"""Qualification gating — distinct from raw experience_fit scoring.

A hard-required requirement with zero supporting evidence dominates
everything else, regardless of how strong the rest of the match is (see
docs/eligibility.md#qualification-gates) — this is what correctly fails
a role that's otherwise a great PHP/API match but requires 3+ years of
professional Go with no Go evidence anywhere in career history. Preferred
qualifications never behave like gates: they're excluded from the
averaging entirely, not just down-weighted.
"""

from careers_os.domain.experience import ExperienceFitDetail
from careers_os.domain.matching import MatchType
from careers_os.domain.qualification import QualificationResult, QualificationStatus
from careers_os.domain.requirements import RequirementImportance

_MATCH_SCORE = {
    MatchType.STRONG_MATCH: 1.0,
    MatchType.PARTIAL_MATCH: 0.6,
    MatchType.ADJACENT_EXPERIENCE: 0.35,
    MatchType.UNSUPPORTED: 0.0,
}


def compute_qualification(detail: ExperienceFitDetail) -> QualificationResult:
    if not detail.resume_available:
        return QualificationResult(
            status=QualificationStatus.UNKNOWN,
            reason="No resume imported yet — qualification cannot be assessed against real evidence.",
        )

    hard_matches = [
        m for m in detail.requirement_matches
        if m.requirement.importance == RequirementImportance.HARD_REQUIRED
    ]
    unsupported_hard_gaps = [m for m in hard_matches if m.match_type == MatchType.UNSUPPORTED]
    adjacent_hard_gaps = [m for m in hard_matches if m.match_type == MatchType.ADJACENT_EXPERIENCE]

    if unsupported_hard_gaps:
        names = ", ".join(m.requirement.text for m in unsupported_hard_gaps)
        return QualificationResult(
            status=QualificationStatus.FAIL,
            hard_gaps=unsupported_hard_gaps,
            reason=f"Hard requirement(s) with no supporting evidence at all: {names}.",
        )

    # Preferred qualifications never gate or drag down qualification —
    # only required/hard-required requirements are averaged.
    non_preferred = [
        m for m in detail.requirement_matches
        if m.requirement.importance != RequirementImportance.PREFERRED and m.match_type in _MATCH_SCORE
    ]
    if not non_preferred:
        return QualificationResult(
            status=QualificationStatus.UNKNOWN,
            reason="No taxonomy-recognized required/hard-required requirements to judge.",
        )

    avg = sum(_MATCH_SCORE[m.match_type] for m in non_preferred) / len(non_preferred)

    if adjacent_hard_gaps:
        names = ", ".join(m.requirement.text for m in adjacent_hard_gaps)
        return QualificationResult(
            status=QualificationStatus.WEAK,
            hard_gaps=adjacent_hard_gaps,
            reason=f"Hard requirement(s) with only adjacent (not direct) evidence: {names}.",
        )

    if avg >= 0.75:
        status, reason = QualificationStatus.STRONG, "Strong direct evidence across required qualifications."
    elif avg >= 0.5:
        status, reason = QualificationStatus.MODERATE, "Moderate evidence across required qualifications."
    else:
        status, reason = QualificationStatus.WEAK, "Weak evidence across required qualifications."

    return QualificationResult(status=status, hard_gaps=[], reason=reason)
