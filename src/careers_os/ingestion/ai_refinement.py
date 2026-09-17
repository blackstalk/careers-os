"""Bounded AI refinement of finalists (Phase 4.3).

Every discovered job is evaluated deterministically. Claude is consulted
only afterwards, only for a short, already-ranked list of finalists (the
opportunities that would otherwise be alerted, or the top of `jobs
discover`), and only up to `preferences.ai_refinement.max_refinements_per_run`
new API calls per run. Past that budget a finalist simply keeps its
deterministic evaluation — nothing fails.

A review is stored per (job, content hash, prompt version)
(storage AIRefinementRecord) and reused on later runs for free, so a
finalist that stays deferred for a week is reviewed once, not five times.

The review can only refine `role_fit` / `career_direction_fit` through the
existing `scoring.ai.apply_ai_evaluation` guardrails; the decision is then
recomputed by the normal deterministic pipeline, so every hard gate
(eligibility, qualification, career track) still applies. See
docs/scoring.md#bounded-ai-refinement.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from careers_os.career.candidate import CandidateProfile
from careers_os.career.discovery_ranking import compute_rank_score
from careers_os.career.evidence_index import EvidenceIndex
from careers_os.career.preferences import Preferences
from careers_os.career.profile import CareerProfile
from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.job import NormalizedJob
from careers_os.domain.opportunity_decision import PursueRecommendation, PursueResult
from careers_os.ingestion.discovery import DiscoveryOpportunity
from careers_os.ingestion.evaluation import evaluate_opportunity
from careers_os.scoring import ai
from careers_os.storage.repository import JobRepository, job_record_to_normalized

logger = logging.getLogger("careers_os.ingestion.ai_refinement")

Evaluator = Callable[[NormalizedJob, CareerProfile], Optional[dict]]

_SCORE_FIELDS = ("role_fit_score", "career_direction_score")

# A review this low means "this job isn't really what it looks like"
# (wrong primary stack, account management in disguise, ...): cap it at
# consider, the same as an off-track title. Middling reviews only lower
# career direction, which by itself turns strong_pursue into pursue.
POOR_FIT_BELOW = 0.4
_CAPPED = (PursueRecommendation.STRONG_PURSUE, PursueRecommendation.PURSUE)


@dataclass
class AIRefinementStats:
    status: str = "not run"          # "ran", or why it didn't
    budget: int = 0                  # max new API calls this run
    finalists: int = 0               # opportunities eligible for review
    calls: int = 0                   # new API calls made
    reused: int = 0                  # stored reviews applied without a call
    failures: int = 0                # calls that errored or returned unusable output
    over_budget: int = 0             # finalists left deterministic because the budget ran out
    changed: list[str] = field(default_factory=list)  # "<title>: before -> after"


def content_hash(job: NormalizedJob) -> str:
    text = f"{job.title}\n{job.company or ''}\n{job.description or ''}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _usable(result: object) -> Optional[dict]:
    """Keep only a well-formed review: both scores present and in [0, 1]."""
    if not isinstance(result, dict):
        return None
    try:
        scores = {k: float(result[k]) for k in _SCORE_FIELDS}
    except (KeyError, TypeError, ValueError):
        return None
    if not all(0.0 <= v <= 1.0 for v in scores.values()):
        return None
    return {**result, **scores}


def refine_finalists(
    finalists: list[DiscoveryOpportunity],
    *,
    repository: JobRepository,
    budget: int,
    profile: Optional[CareerProfile] = None,
    preferences: Optional[Preferences] = None,
    candidate: Optional[CandidateProfile] = None,
    taxonomy: Optional[SkillsTaxonomy] = None,
    evidence_index: Optional[EvidenceIndex] = None,
    evaluator: Optional[Evaluator] = None,
) -> AIRefinementStats:
    """Review `finalists` in the given (ranked) order, updating each
    reviewed opportunity's `fit`, `immediate`, `direction`, and `decision`
    in place. `evaluator` defaults to Claude (`scoring.ai.evaluate`) when
    it is configured; tests pass a fake.
    """
    stats = AIRefinementStats(budget=max(budget, 0), finalists=len(finalists))
    if evaluator is None:
        ready, why = ai.readiness()
        if not ready:
            # Stored reviews are still applied: they cost nothing.
            stats.status = f"no new reviews: {why}"
            evaluator = None
        else:
            evaluator = ai.evaluate
            stats.status = "ran"
    else:
        stats.status = "ran"

    profile = profile or CareerProfile.load()
    preferences = preferences or Preferences.load()

    for opp in finalists:
        job = job_record_to_normalized(opp.job)
        key = content_hash(job)
        review = repository.get_ai_refinement(opp.job.id, key, ai.PROMPT_VERSION)
        if review is not None:
            stats.reused += 1
        elif evaluator is None:
            continue
        elif stats.calls >= stats.budget:
            stats.over_budget += 1
            continue
        else:
            stats.calls += 1
            try:
                review = _usable(evaluator(job, profile))
            except Exception as exc:  # noqa: BLE001 - a review must never take down the run
                logger.warning("ai_refinement.failed", extra={"job_id": opp.job.id, "error": str(exc)})
                review = None
            if review is None:
                stats.failures += 1
                continue
            repository.save_ai_refinement(opp.job.id, key, ai.PROMPT_VERSION, ai.MODEL, review)

        before = opp.decision.pursue.recommendation
        refined_fit = ai.apply_ai_evaluation(opp.fit, review)
        result = evaluate_opportunity(
            job, refined_fit, candidate=candidate, preferences=preferences,
            taxonomy=taxonomy, evidence_index=evidence_index, use_ai=False,
        )
        decision = result.decision
        if review["role_fit_score"] < POOR_FIT_BELOW and decision.pursue.recommendation in _CAPPED:
            why = review.get("role_fit_reason") or "the role's responsibilities don't match the candidate"
            decision = decision.model_copy(update={"pursue": PursueResult(
                recommendation=PursueRecommendation.CONSIDER,
                reason=f"Capped at consider by AI review: {why}",
                contributing_factors=[*decision.pursue.contributing_factors, "ai_review=poor_fit"],
            )})
        opp.fit, opp.immediate, opp.direction, opp.decision = result.fit, result.immediate, result.direction, decision
        opp.rank_score = compute_rank_score(
            opp.fit, opp.bridge, opp.immediate, opp.direction, preferences.discovery_ranking_weights
        )
        after = opp.decision.pursue.recommendation
        if after != before:
            stats.changed.append(f"{opp.job.title} ({opp.job.company or 'unknown'}): {before.value} -> {after.value}")

    repository.commit()
    logger.info(
        "ai_refinement.completed",
        extra={
            "status": stats.status, "budget": stats.budget, "finalists": stats.finalists,
            "calls": stats.calls, "reused": stats.reused, "failures": stats.failures,
            "over_budget": stats.over_budget, "changed": len(stats.changed),
        },
    )
    return stats
