from typing import Optional

from careers_os.career.preferences import Preferences
from careers_os.career.profile import CareerProfile
from careers_os.domain.job import NormalizedJob
from careers_os.domain.scoring import CareerFitResult
from careers_os.scoring import ai, deterministic
from careers_os.scoring.evidence_matcher import EvidenceIndex, build_experience_fit_detail
from careers_os.scoring.evidence_matcher import score_experience_fit as score_experience_fit_from_evidence
from careers_os.scoring.requirements import extract_requirements


def _overall_fit(result: CareerFitResult, weights: dict[str, float]) -> float:
    components = result.components()
    total_weight = sum(weights.values()) or 1.0
    weighted = sum(components[name].score * weight for name, weight in weights.items())
    return round(weighted / total_weight, 3)


def score_job(
    job: NormalizedJob,
    profile: CareerProfile,
    preferences: Preferences,
    *,
    use_ai: bool = True,
    evidence_index: Optional[EvidenceIndex] = None,
) -> CareerFitResult:
    """Compute an explainable career-fit score for a normalized job.

    Deterministic scoring always runs and is always sufficient on its own
    (no API key required). If `use_ai` and an AI evaluator is configured,
    its output refines role_fit/career_direction_fit — see scoring/ai.py.

    `experience_fit` uses real career evidence (scoring/evidence_matcher.py)
    when `evidence_index` is supplied and has any imported evidence;
    otherwise it falls back to the honest "no resume imported" placeholder
    (deterministic.score_experience_fit) — see docs/scoring.md.
    """
    experience_detail = None
    if evidence_index is not None and evidence_index.has_any_evidence():
        requirements = extract_requirements(job, evidence_index.taxonomy)
        experience_detail = build_experience_fit_detail(requirements, evidence_index)
        experience_fit = score_experience_fit_from_evidence(experience_detail)
    else:
        experience_fit = deterministic.score_experience_fit(job)

    result = CareerFitResult(
        role_fit=deterministic.score_role_fit(job, profile),
        technical_fit=deterministic.score_technical_fit(job, profile),
        career_direction_fit=deterministic.score_career_direction_fit(job, profile, experience_detail),
        compensation_fit=deterministic.score_compensation_fit(job, preferences),
        work_arrangement_fit=deterministic.score_work_arrangement_fit(job, preferences),
        experience_fit=experience_fit,
        experience_detail=experience_detail,
        overall_fit=0.0,
        scorer_version=deterministic.SCORER_VERSION,
    )

    if use_ai and ai.is_available():
        ai_result = ai.evaluate(job, profile)
        if ai_result:
            result = ai.apply_ai_evaluation(result, ai_result)

    result.overall_fit = _overall_fit(result, preferences.component_weights.as_dict())
    return result
