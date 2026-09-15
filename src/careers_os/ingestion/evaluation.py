"""Opportunity decision orchestration (Phase 3).

This is the one place allowed to know about every decision-layer piece —
eligibility, qualification, AI evidence reasoning, opportunity cost,
scope, freshness, and the final pursue recommendation — mirroring how
ingestion/pipeline.py is the one place that knows about a source adapter,
the repository, and the scoring engine. Nothing here recomputes
`CareerFitResult`; it's passed in already-scored (see cli.py), and this
module only adds the decision layer on top of it.
"""

from dataclasses import dataclass
from typing import Optional

from careers_os.career.bridge_role import BridgeRoleResult, classify_bridge_role
from careers_os.career.candidate import CandidateProfile
from careers_os.career.eligibility import evaluate_eligibility
from careers_os.career.evidence_index import EvidenceIndex
from careers_os.career.freshness import assess_freshness
from careers_os.career.opportunity_cost import assess_opportunity_cost
from careers_os.career.opportunity_value import (
    OpportunityAssessment,
    assess_career_direction,
    assess_immediate_opportunity,
)
from careers_os.career.preferences import Preferences
from careers_os.career.pursue import compute_pursue_recommendation
from careers_os.career.scope import classify_scope
from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.job import NormalizedJob
from careers_os.domain.opportunity_decision import OpportunityDecision
from careers_os.domain.qualification import QualificationResult, QualificationStatus
from careers_os.domain.scoring import CareerFitResult
from careers_os.scoring.evidence_reasoner import EvidenceReasoner, apply_evidence_reasoning
from careers_os.scoring.qualification import compute_qualification


@dataclass
class EvaluationResult:
    decision: OpportunityDecision
    fit: CareerFitResult  # experience_detail may carry AI annotations added here
    bridge: BridgeRoleResult
    immediate: OpportunityAssessment
    direction: OpportunityAssessment


def evaluate_opportunity(
    job: NormalizedJob,
    fit: CareerFitResult,
    *,
    candidate: Optional[CandidateProfile] = None,
    preferences: Optional[Preferences] = None,
    taxonomy: Optional[SkillsTaxonomy] = None,
    evidence_index: Optional[EvidenceIndex] = None,
    use_ai: bool = True,
    reasoner: Optional[EvidenceReasoner] = None,
) -> EvaluationResult:
    candidate = candidate or CandidateProfile.load()
    preferences = preferences or Preferences.load()
    taxonomy = taxonomy or SkillsTaxonomy.load()

    eligibility = evaluate_eligibility(job, candidate)

    detail = fit.experience_detail
    if use_ai and detail is not None and evidence_index is not None:
        detail = apply_evidence_reasoning(job.title, job.description, detail, evidence_index, reasoner)
        fit = fit.model_copy(update={"experience_detail": detail})

    if detail is not None:
        qualification = compute_qualification(detail)
    else:
        qualification = QualificationResult(
            status=QualificationStatus.UNKNOWN,
            reason="No resume imported yet — qualification cannot be assessed against real evidence.",
        )

    immediate = assess_immediate_opportunity(fit)
    direction = assess_career_direction(fit)
    bridge = classify_bridge_role(job.title, job.description, taxonomy)
    opportunity_cost = assess_opportunity_cost(qualification, fit.compensation_fit, direction)
    scope = classify_scope(job.title, job.description, taxonomy)
    freshness = assess_freshness(job.posted_at, preferences.freshness_thresholds)
    pursue = compute_pursue_recommendation(eligibility, qualification, immediate, direction, opportunity_cost)

    decision = OpportunityDecision(
        eligibility=eligibility,
        qualification=qualification,
        opportunity_cost=opportunity_cost,
        scope=scope,
        freshness=freshness,
        pursue=pursue,
    )
    return EvaluationResult(decision=decision, fit=fit, bridge=bridge, immediate=immediate, direction=direction)
