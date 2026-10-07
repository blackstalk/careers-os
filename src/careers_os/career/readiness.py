"""Readiness: how ready is the candidate for this job, today (Phase 5).

`pursue` answers "is this worth pursuing?" by weighing qualification,
career direction, immediate opportunity, and opportunity cost together.
That is the right question for the exploratory track and the wrong one
for the replacement objective, which needs a blunter distinction:

- `immediate_fit`      credible now, nothing material unresolved
- `stretch`            real gaps, but a manageable number
- `learning_target`    substantial gaps; a direction to grow toward
- `needs_verification` unresolved eligibility or unconfirmed pay
- `not_a_fit`          ineligible, or a failed hard qualification gate

Structured categories rather than another opaque score: every outcome
names the specific evidence behind it. Compensation, prestige, and
long-term career alignment are deliberately absent as *upgrades* here —
they cannot turn a substantial core gap into readiness. See
docs/pursue-recommendation.md#readiness.
"""

from typing import Optional

from careers_os.domain.compensation import CompensationAssessment
from careers_os.domain.eligibility import EligibilityResult, EligibilityStatus
from careers_os.domain.enums import SearchTrack
from careers_os.domain.experience import ExperienceFitDetail
from careers_os.domain.matching import MatchType
from careers_os.domain.opportunity_decision import Readiness, ReadinessResult
from careers_os.domain.qualification import QualificationResult, QualificationStatus

# Three or more unresolved core signals stops being a stretch.
_SUBSTANTIAL_GAP_COUNT = 3


def _core_evidence(detail: Optional[ExperienceFitDetail]) -> tuple[int, int]:
    """(core requirements found, core requirements the candidate can
    actually evidence). Both zero means the posting's requirements could
    not be parsed at all — uncertainty, not a clean bill of health."""
    if detail is None:
        return 0, 0
    core = [m for m in detail.requirement_matches if m.requirement.is_core]
    supported = [m for m in core if m.match_type in (MatchType.STRONG_MATCH, MatchType.PARTIAL_MATCH)]
    return len(core), len(supported)


def _core_gaps(detail: Optional[ExperienceFitDetail]) -> tuple[list[str], list[str]]:
    """(unsupported, thin) core requirement names. Thin means adjacent
    evidence only: plausible transfer, not demonstrated experience."""
    if detail is None:
        return [], []
    unsupported = [
        m.requirement.text for m in detail.requirement_matches
        if m.requirement.is_core and m.match_type == MatchType.UNSUPPORTED
    ]
    thin = [
        m.requirement.text for m in detail.requirement_matches
        if m.requirement.is_core and m.match_type == MatchType.ADJACENT_EXPERIENCE
    ]
    return unsupported, thin


def assess_readiness(
    eligibility: EligibilityResult,
    qualification: QualificationResult,
    detail: Optional[ExperienceFitDetail],
    compensation: CompensationAssessment,
    *,
    track: SearchTrack = SearchTrack.REPLACEMENT,
) -> ReadinessResult:
    unsupported, thin = _core_gaps(detail)
    unmatched = list(detail.unmatched_core_requirements) if detail else []
    if eligibility.status == EligibilityStatus.INELIGIBLE:
        blocking = "; ".join(c.requirement for c in eligibility.blocking_checks)
        return ReadinessResult(
            level=Readiness.NOT_A_FIT, reason=f"Ineligible: {blocking}.",
            core_gaps=unsupported, thin_core_gaps=thin, unverified_requirements=unmatched,
        )
    if qualification.status == QualificationStatus.FAIL:
        return ReadinessResult(
            level=Readiness.NOT_A_FIT, reason=qualification.reason,
            core_gaps=unsupported, thin_core_gaps=thin, unverified_requirements=unmatched,
        )

    if eligibility.status in (EligibilityStatus.VERIFY, EligibilityStatus.UNKNOWN):
        checks = "; ".join(c.requirement for c in (eligibility.verify_checks + eligibility.unknown_checks))
        return ReadinessResult(
            level=Readiness.NEEDS_VERIFICATION,
            reason=f"Eligibility needs confirmation: {checks}.",
            core_gaps=unsupported, thin_core_gaps=thin, unverified_requirements=unmatched,
        )
    # Phase 5.1: affirmative core evidence is required before anything
    # can be called an immediate fit. A posting whose requirements could
    # not be parsed (no heading, prose-only, a two-line listing) used to
    # yield "no core gaps" and read as a clean match; absence of parsed
    # requirements is uncertainty, not success. Qualification can still
    # be STRONG off incidental mentions, and that must not become
    # replacement confidence on its own.
    core_found, core_supported = _core_evidence(detail)
    if core_supported == 0:
        reason = (
            "No requirements could be parsed from this posting, so there is nothing to check "
            "the candidate's evidence against."
            if core_found == 0 else
            "None of the posting's core requirements are evidenced in imported career history."
        )
        return ReadinessResult(
            level=Readiness.NEEDS_VERIFICATION, reason=reason,
            core_gaps=unsupported, thin_core_gaps=thin, unverified_requirements=unmatched,
        )

    # Pay only gates the replacement objective: the exploratory track is
    # about direction, not about replacing current income.
    if track == SearchTrack.REPLACEMENT and compensation.needs_verification:
        return ReadinessResult(
            level=Readiness.NEEDS_VERIFICATION, reason=compensation.reason,
            core_gaps=unsupported, thin_core_gaps=thin, unverified_requirements=unmatched,
        )

    if len(unsupported) + len(unmatched) >= _SUBSTANTIAL_GAP_COUNT or (
        qualification.status == QualificationStatus.WEAK
    ):
        detail_bits = [*unsupported, *unmatched][:3]
        return ReadinessResult(
            level=Readiness.LEARNING_TARGET,
            reason="Substantial unmet core requirements: " + "; ".join(detail_bits)
            if detail_bits else qualification.reason,
            core_gaps=unsupported, thin_core_gaps=thin, unverified_requirements=unmatched,
        )

    if unsupported or thin or unmatched or qualification.status != QualificationStatus.STRONG:
        named = [*unsupported, *thin, *unmatched][:3]
        return ReadinessResult(
            level=Readiness.STRETCH,
            reason=("Manageable gaps: " + "; ".join(named)) if named
            else f"Qualification is {qualification.status.value}, short of fully evidenced.",
            core_gaps=unsupported, thin_core_gaps=thin, unverified_requirements=unmatched,
        )

    return ReadinessResult(
        level=Readiness.IMMEDIATE_FIT,
        reason="Core requirements are directly evidenced, eligibility is clear"
        + (", and pay is confirmed above target." if compensation.confirmed else "."),
    )
