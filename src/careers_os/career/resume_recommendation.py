"""Rule-based resume-variant recommendation.

Given a job's requirement matches, recommend which imported resume variant
cited the most (and strongest) evidence for those matches. Deliberately
simple — a weighted coverage count, not a learned ranking — and entirely
explainable: the reason lists exactly which requirements tipped the
decision. See docs/evidence-model.md#resume-variant-recommendation.
"""

from dataclasses import dataclass, field

from careers_os.domain.experience import ExperienceFitDetail
from careers_os.domain.matching import MatchType

_MATCH_WEIGHT = {
    MatchType.STRONG_MATCH: 1.0,
    MatchType.PARTIAL_MATCH: 0.6,
    MatchType.ADJACENT_EXPERIENCE: 0.35,
}


@dataclass
class ResumeRecommendation:
    recommended_variant: str | None
    scores: dict[str, float]
    reasons: dict[str, list[str]] = field(default_factory=dict)
    note: str = ""


def recommend_resume_variant(detail: ExperienceFitDetail) -> ResumeRecommendation:
    scores: dict[str, float] = {}
    reasons: dict[str, list[str]] = {}

    for match in detail.requirement_matches:
        weight = _MATCH_WEIGHT.get(match.match_type)
        if weight is None or not match.matched_evidence:
            continue
        variants_cited = {ref.variant_slug for ref in match.matched_evidence if ref.variant_slug}
        for variant in variants_cited:
            scores[variant] = scores.get(variant, 0.0) + weight
            reasons.setdefault(variant, [])
            if len(reasons[variant]) < 5:
                reasons[variant].append(match.requirement.text)

    if not scores:
        return ResumeRecommendation(
            recommended_variant=None,
            scores={},
            note="No resume variant's evidence was cited in any requirement match "
            "(no resume imported, or nothing in this job matched taxonomy skills).",
        )

    best = max(scores, key=lambda v: scores[v])
    return ResumeRecommendation(
        recommended_variant=best,
        scores=dict(sorted(scores.items(), key=lambda kv: kv[1], reverse=True)),
        reasons=reasons,
        note=f"'{best}' contributed evidence to more/stronger requirement matches than "
        "the alternative(s) for this specific job.",
    )
