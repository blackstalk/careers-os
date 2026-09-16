"""Rule-based, explainable scoring components.

No LLM required. Every function here returns a `FitComponent` with a score,
a human-readable reason, concrete evidence, and a confidence that is honest
about missing data — see docs/scoring.md for the design rationale.
"""

from typing import Optional

from careers_os.career.bridge_role import BRIDGE_SIGNAL_CATEGORIES
from careers_os.career.profile import CareerProfile
from careers_os.career.preferences import Preferences
from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.experience import ExperienceFitDetail
from careers_os.domain.job import NormalizedJob
from careers_os.domain.matching import MatchType
from careers_os.domain.scoring import FitComponent

SCORER_VERSION = "deterministic-v1"

# Mirrors scoring/qualification.py's _MATCH_SCORE mapping — duplicated
# rather than imported so this module doesn't reach into another
# module's private constant; same semantics, same four MatchType values.
_EVIDENCE_MATCH_SCORE = {
    MatchType.STRONG_MATCH: 1.0,
    MatchType.PARTIAL_MATCH: 0.6,
    MatchType.ADJACENT_EXPERIENCE: 0.35,
    MatchType.UNSUPPORTED: 0.0,
}


def _keyword_matches(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    return [kw for kw in keywords if kw.lower() in lowered]


def _keyword_score(matched: list[str], saturate_at: int = 3) -> float:
    if not matched:
        return 0.0
    return min(1.0, len(matched) / saturate_at)


def score_role_fit(job: NormalizedJob, profile: CareerProfile) -> FitComponent:
    text = f"{job.title}\n{job.description or ''}"
    matched = _keyword_matches(text, profile.target_role_keywords)
    title_matched = _keyword_matches(job.title, profile.target_role_keywords)

    score = _keyword_score(matched)
    if title_matched:
        score = min(1.0, score + 0.2)  # title match is a stronger signal than a body mention

    has_full_text = bool(job.description and len(job.description) > 200)
    confidence = 0.8 if has_full_text else 0.45

    if matched:
        reason = (
            f"Job title/description mentions {len(matched)} term(s) associated with "
            f"target roles ({', '.join(profile.target_roles[:3])}, ...)."
        )
    else:
        reason = "No target-role terminology found in the available title/description text."

    return FitComponent(
        score=score,
        reason=reason,
        evidence=[f"matched: {m}" for m in matched],
        confidence=confidence,
    )


def score_technical_fit(job: NormalizedJob, profile: CareerProfile) -> FitComponent:
    text = f"{job.title}\n{job.description or ''}\n{' '.join(job.skills)}"
    matched = _keyword_matches(text, profile.technical_interest_keywords)
    score = _keyword_score(matched, saturate_at=4)

    has_full_text = bool(job.description and len(job.description) > 200)
    confidence = 0.75 if has_full_text else 0.4

    reason = (
        f"{len(matched)} technical-interest term(s) found in title/description/skills."
        if matched
        else "No technical-interest terminology found in the available text."
    )
    return FitComponent(
        score=score,
        reason=reason,
        evidence=[f"matched: {m}" for m in matched],
        confidence=confidence,
    )


def forward_direction_matches(experience_detail: Optional[ExperienceFitDetail]) -> list:
    """Requirement matches in a forward-direction category that carry a
    real match judgment — the evidence `score_career_direction_fit` is
    grounded in. Empty means the keyword fallback was used.
    """
    if experience_detail is None:
        return []
    return [
        m
        for m in experience_detail.requirement_matches
        if m.requirement.category in BRIDGE_SIGNAL_CATEGORIES and m.match_type in _EVIDENCE_MATCH_SCORE
    ]


def score_career_direction_fit(
    job: NormalizedJob, profile: CareerProfile, experience_detail: Optional[ExperienceFitDetail] = None
) -> FitComponent:
    """Whether this posting represents a *credible transition*, not just a
    title/keyword resemblance — see docs/scoring.md#career-direction-fit.

    Two distinct signals, in priority order:

    1. Real evidence overlap: when `experience_detail` shows the
       candidate's own evidence actually matching this job's extracted
       requirements in a forward-direction category (architecture, cloud,
       ai_ml, leadership, customer_facing — the same categories
       career/bridge_role.py already uses), the score comes directly from
       those match strengths. A job titled "Applied AI Engineer" whose
       requirements the candidate has no real (non-adjacent) evidence
       for scores low here regardless of how many AI/FDE buzzwords the
       posting itself uses — company/title prestige plays no role.
    2. Job-text keyword presence (the original heuristic): used only when
       no forward-direction-category requirements were extracted at all
       (no resume imported, or this specific posting's extraction found
       none) — preserves prior behavior exactly in that case, at the same
       lower confidence as before.
    """
    forward_matches = forward_direction_matches(experience_detail)

    if forward_matches:
        evidence_score = sum(_EVIDENCE_MATCH_SCORE[m.match_type] for m in forward_matches) / len(forward_matches)
        categories = sorted({m.requirement.category.value for m in forward_matches})
        category_label = "category" if len(categories) == 1 else "categories"
        reason = (
            f"Real evidence overlap in {len(categories)} target-direction {category_label} "
            f"({', '.join(categories)}) — based on matched candidate evidence, not title/keyword "
            "resemblance."
        )
        return FitComponent(
            score=round(evidence_score, 3),
            reason=reason,
            evidence=[f"{m.requirement.category.value}: {m.match_type.value}" for m in forward_matches],
            confidence=0.8,
        )

    text = f"{job.title}\n{job.description or ''}"
    matched = _keyword_matches(text, profile.career_direction_keywords)
    score = _keyword_score(matched, saturate_at=2)

    has_full_text = bool(job.description and len(job.description) > 200)
    confidence = 0.7 if has_full_text else 0.35

    reason = (
        f"Mentions {len(matched)} term(s) aligned with target career direction "
        "(no matched candidate evidence available to judge this more directly)."
        if matched
        else "Nothing in the available text signals movement toward the target career direction."
    )
    return FitComponent(
        score=score,
        reason=reason,
        evidence=[f"matched: {m}" for m in matched],
        confidence=confidence,
    )


def score_compensation_fit(job: NormalizedJob, preferences: Preferences) -> FitComponent:
    comp = preferences.compensation

    if job.employment_type == EmploymentType.FULL_TIME:
        if job.salary_min is None and job.salary_max is None:
            return FitComponent(
                score=0.5,
                reason="No salary published for this full-time role; treating as unknown "
                "rather than penalizing — do not reject strategically interesting roles "
                "solely for missing compensation data.",
                evidence=[],
                confidence=0.15,
            )
        figure = job.salary_max or job.salary_min
        if figure >= comp.full_time.strong_annual:
            score = 1.0
        elif figure >= comp.full_time.minimum_annual:
            span = comp.full_time.strong_annual - comp.full_time.minimum_annual
            score = 0.6 + 0.4 * ((figure - comp.full_time.minimum_annual) / span if span else 1)
        else:
            score = max(0.0, 0.6 * (figure / comp.full_time.minimum_annual))
        return FitComponent(
            score=round(score, 3),
            reason=f"Published salary (~${figure:,.0f}) compared against target range "
            f"(${comp.full_time.minimum_annual:,.0f} min / ${comp.full_time.strong_annual:,.0f} strong).",
            evidence=[f"salary_max={job.salary_max}", f"salary_min={job.salary_min}"],
            confidence=0.85,
        )

    if job.employment_type in (EmploymentType.CONTRACT, EmploymentType.FREELANCE):
        if job.hourly_min is None and job.hourly_max is None:
            return FitComponent(
                score=0.5,
                reason="No hourly rate published for this contract/freelance role; treating "
                "as unknown rather than penalizing.",
                evidence=[],
                confidence=0.15,
            )
        figure = job.hourly_max or job.hourly_min
        if figure >= comp.contract.exceptional_hourly:
            score = 1.0
        elif figure >= comp.contract.strong_hourly:
            span = comp.contract.exceptional_hourly - comp.contract.strong_hourly
            score = 0.8 + 0.2 * ((figure - comp.contract.strong_hourly) / span if span else 1)
        elif figure >= comp.contract.minimum_hourly:
            span = comp.contract.strong_hourly - comp.contract.minimum_hourly
            score = 0.5 + 0.3 * ((figure - comp.contract.minimum_hourly) / span if span else 1)
        else:
            score = max(0.0, 0.5 * (figure / comp.contract.minimum_hourly))
        return FitComponent(
            score=round(score, 3),
            reason=f"Published hourly rate (~${figure:,.2f}/hr) compared against target range "
            f"(${comp.contract.minimum_hourly:.0f}/hr min / ${comp.contract.strong_hourly:.0f}+/hr strong).",
            evidence=[f"hourly_max={job.hourly_max}", f"hourly_min={job.hourly_min}"],
            confidence=0.85,
        )

    return FitComponent(
        score=0.5,
        reason="Employment type unknown; cannot compare compensation against a target range.",
        evidence=[],
        confidence=0.1,
    )


def score_work_arrangement_fit(job: NormalizedJob, preferences: Preferences) -> FitComponent:
    wa = preferences.work_arrangement
    if job.remote_status == RemoteStatus.REMOTE:
        return FitComponent(
            score=1.0,
            reason="Fully remote, the preferred arrangement.",
            evidence=["remote_status=remote"],
            confidence=0.95,
        )
    if job.remote_status == RemoteStatus.HYBRID:
        return FitComponent(
            score=round(1.0 - wa.hybrid_penalty, 3),
            reason="Hybrid — acceptable, and can be strategically compelling, but not preferred "
            "over fully remote.",
            evidence=["remote_status=hybrid"],
            confidence=0.9,
        )
    if job.remote_status == RemoteStatus.ONSITE:
        return FitComponent(
            score=round(1.0 - wa.onsite_penalty, 3),
            reason="Onsite — least preferred arrangement, but not disqualifying.",
            evidence=["remote_status=onsite"],
            confidence=0.9,
        )
    return FitComponent(
        score=0.5,
        reason="Work arrangement not stated by the source; treating as unknown.",
        evidence=[],
        confidence=0.1,
    )


def score_experience_fit(job: NormalizedJob) -> FitComponent:
    """Fallback used only when no resume has been imported (no evidence to
    compare against). Once a resume is imported, `scoring/engine.py` uses
    `scoring/evidence_matcher.py`'s real, evidence-based experience_fit
    instead — see docs/scoring.md#experience-fit.
    """
    return FitComponent(
        score=0.5,
        reason="No resume imported yet — experience fit cannot be reliably inferred from "
        "job-listing text alone. Run `careers import-resume` to enable real evidence-based "
        "matching instead of this placeholder.",
        evidence=[],
        confidence=0.05,
    )
