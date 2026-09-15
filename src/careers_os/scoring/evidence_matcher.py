"""Deterministic matching of job requirements against career evidence.

This is the piece that turns "job ingestion + generic fit scoring" into
"personal career intelligence" — see docs/evidence-model.md. Every
decision here is a plain rule over data that was actually imported; no
step here invents experience. The optional AI layer (scoring/ai.py) is the
only place semantic judgment enters, and even there it's required to cite
evidence IDs (see docs/evidence-model.md#ai-guardrails).
"""

from careers_os.career.evidence_index import EvidenceIndex
from careers_os.career.timeline import total_career_span_years, years_for_skill
from careers_os.domain.evidence import Evidence
from careers_os.domain.experience import ExperienceFitDetail, YearsEstimate
from careers_os.domain.matching import MATCH_TYPE_TO_GAP_TYPE, EvidenceRef, MatchType, RequirementMatch
from careers_os.domain.requirements import JobRequirement
from careers_os.domain.scoring import FitComponent

_MAX_CITED_EVIDENCE = 3


def _evidence_refs(items: list[Evidence]) -> list[EvidenceRef]:
    # Dated evidence first — it's what years-of-experience claims rest on.
    ordered = sorted(items, key=lambda e: e.start_date is None)
    return [
        EvidenceRef(
            evidence_id=e.id,
            statement=e.statement,
            company=e.provenance.company,
            variant_slug=e.provenance.variant_slug,
        )
        for e in ordered[:_MAX_CITED_EVIDENCE]
    ]


def match_requirement(requirement: JobRequirement, index: EvidenceIndex) -> RequirementMatch:
    if requirement.canonical_skill is None:
        return RequirementMatch(
            requirement=requirement,
            match_type=MatchType.UNKNOWN,
            reason="This requirement didn't map to anything in the skills taxonomy — "
            "not enough structure to judge deterministically. A candidate for optional "
            "AI review, not a scored gap.",
            gap_type=MATCH_TYPE_TO_GAP_TYPE[MatchType.UNKNOWN],
        )

    skill = requirement.canonical_skill
    direct = index.evidence_for_skill(skill)
    display_name = index.taxonomy.display_name(skill)

    if direct:
        if requirement.min_years:
            estimate = years_for_skill(skill, index.evidence, index.career_roles)
            if estimate.numeric_years >= requirement.min_years:
                match_type = MatchType.STRONG_MATCH
                reason = (
                    f"Evidence shows ~{estimate.display_years} of {display_name}, meeting the "
                    f"stated {requirement.min_years}+ year requirement."
                )
            else:
                match_type = MatchType.PARTIAL_MATCH
                reason = (
                    f"{display_name} is directly evidenced, but estimated duration "
                    f"({estimate.display_years}) is short of the stated "
                    f"{requirement.min_years}+ year requirement."
                )
        else:
            match_type = MatchType.STRONG_MATCH
            reason = f"{display_name} is directly evidenced in imported career history."
        return RequirementMatch(
            requirement=requirement,
            match_type=match_type,
            matched_evidence=_evidence_refs(direct),
            reason=reason,
            gap_type=MATCH_TYPE_TO_GAP_TYPE[match_type],
        )

    adjacent_keys = index.taxonomy.adjacent_to(skill)
    adjacent_hits = [(k, index.evidence_for_skill(k)) for k in adjacent_keys]
    adjacent_hits = [(k, items) for k, items in adjacent_hits if items]

    if len(adjacent_hits) >= 2:
        match_type = MatchType.PARTIAL_MATCH
        names = ", ".join(index.taxonomy.display_name(k) for k, _ in adjacent_hits)
        reason = (
            f"No direct evidence of {display_name}, but {len(adjacent_hits)} closely related "
            f"competencies are evidenced ({names}) — substance likely present under different "
            "framing rather than a real gap."
        )
        cited = [item for _, items in adjacent_hits for item in items]
    elif len(adjacent_hits) == 1:
        match_type = MatchType.ADJACENT_EXPERIENCE
        k, items = adjacent_hits[0]
        reason = (
            f"No direct evidence of {display_name}. One related competency is evidenced "
            f"({index.taxonomy.display_name(k)}) — plausible transferable experience, but thin."
        )
        cited = items
    else:
        match_type = MatchType.UNSUPPORTED
        reason = f"No direct or adjacent evidence found for {display_name} in imported career history."
        cited = []

    return RequirementMatch(
        requirement=requirement,
        match_type=match_type,
        matched_evidence=_evidence_refs(cited),
        reason=reason,
        gap_type=MATCH_TYPE_TO_GAP_TYPE[match_type],
    )


def build_experience_fit_detail(
    requirements: list[JobRequirement], index: EvidenceIndex
) -> ExperienceFitDetail:
    matches = [match_requirement(r, index) for r in requirements]

    years_estimates: dict[str, YearsEstimate] = {}
    for r in requirements:
        if r.canonical_skill and r.canonical_skill not in years_estimates:
            years_estimates[r.canonical_skill] = years_for_skill(
                r.canonical_skill, index.evidence, index.career_roles
            )
    if index.career_roles:
        span = total_career_span_years(index.career_roles)
        years_estimates["overall"] = YearsEstimate(
            skill="overall",
            display_years=_humanize(span),
            numeric_years=span,
            confidence=0.9,
            supporting_companies=sorted({r.company for r in index.career_roles}),
        )

    return ExperienceFitDetail(
        requirement_matches=matches,
        years_estimates=years_estimates,
        resume_available=index.has_any_evidence(),
    )


def _humanize(years: float) -> str:
    from careers_os.career.timeline import humanize_years

    return humanize_years(years)


_MATCH_SCORE = {
    MatchType.STRONG_MATCH: 1.0,
    MatchType.PARTIAL_MATCH: 0.6,
    MatchType.ADJACENT_EXPERIENCE: 0.35,
    MatchType.UNSUPPORTED: 0.0,
}


def score_experience_fit(detail: ExperienceFitDetail) -> FitComponent:
    """Summarize an ExperienceFitDetail into the concise FitComponent shape
    used alongside the other five components (see domain/scoring.py). The
    full breakdown (per-requirement matches, gaps, years estimates) stays
    available on `CareerFitResult.experience_detail` for `jobs match` /
    `jobs evidence` / `jobs gaps` — this is deliberately just the summary.
    """
    if not detail.resume_available:
        return FitComponent(
            score=0.5,
            reason="No resume imported yet — experience fit cannot be evaluated against "
            "real evidence. Run `careers import-resume` first.",
            evidence=[],
            confidence=0.0,
        )

    scored = [m for m in detail.requirement_matches if m.match_type in _MATCH_SCORE]
    if not scored:
        return FitComponent(
            score=0.5,
            reason="No taxonomy-recognized requirements were found in this job's description "
            "to compare against career evidence.",
            evidence=[],
            confidence=0.2,
        )

    avg_score = sum(_MATCH_SCORE[m.match_type] for m in scored) / len(scored)
    resolved_fraction = len(scored) / len(detail.requirement_matches)
    confidence = round(min(0.95, 0.4 + 0.5 * resolved_fraction), 2)

    strong = len(detail.strong_matches)
    partial = len([m for m in scored if m.match_type == MatchType.PARTIAL_MATCH])
    adjacent = len([m for m in scored if m.match_type == MatchType.ADJACENT_EXPERIENCE])
    unsupported = len([m for m in scored if m.match_type == MatchType.UNSUPPORTED])

    reason = (
        f"{strong} strong match(es), {partial} partial, {adjacent} adjacent, "
        f"{unsupported} unsupported out of {len(scored)} taxonomy-recognized requirements."
    )
    evidence = [
        f"{m.requirement.text}: {m.matched_evidence[0].statement}"
        for m in detail.strong_matches[:3]
        if m.matched_evidence
    ]

    return FitComponent(
        score=round(avg_score, 3),
        reason=reason,
        evidence=evidence,
        confidence=confidence,
    )
