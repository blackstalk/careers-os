"""Discovery orchestration (Phase 2.5).

Runs a set of search profiles against a set of sources, reusing the
existing Phase 1/2 pipeline (`run_search_ingestion`) for every individual
(source, query) call — this module adds *ranking and provenance* on top,
it does not reimplement search/normalize/score. See docs/discovery.md.
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from careers_os.career.bridge_role import BridgeClassification, BridgeRoleResult, classify_bridge_role
from careers_os.career.evidence_index import EvidenceIndex
from careers_os.career.opportunity_value import (
    OpportunityAssessment,
    assess_career_direction,
    assess_immediate_opportunity,
)
from careers_os.career.candidate import CandidateProfile
from careers_os.career.discovery_ranking import compute_rank_score
from careers_os.career.preferences import Preferences
from careers_os.career.profile import CareerProfile
from careers_os.career.search_profiles import SearchProfile
from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.enums import EmploymentType
from careers_os.domain.opportunity_decision import OpportunityDecision
from careers_os.domain.query import JobSearchQuery
from careers_os.domain.scoring import CareerFitResult
from careers_os.ingestion.evaluation import evaluate_opportunity
from careers_os.ingestion.pipeline import run_search_ingestion
from careers_os.sources.base import JobSource
from careers_os.storage.db import JobRecord
from careers_os.storage.repository import JobRepository, job_record_to_normalized

if TYPE_CHECKING:
    from careers_os.ingestion.ai_refinement import AIRefinementStats

logger = logging.getLogger("careers_os.ingestion.discovery")


@dataclass
class DiscoveryOpportunity:
    job: JobRecord
    fit: CareerFitResult
    bridge: BridgeRoleResult
    immediate: OpportunityAssessment
    direction: OpportunityAssessment
    matched_profiles: list[str]
    rank_score: float
    decision: OpportunityDecision


@dataclass
class DiscoveryMetrics:
    sources_queried: int = 0
    search_profiles: int = 0
    raw_jobs_discovered: int = 0
    unique_jobs: int = 0
    strong_matches: int = 0
    strong_bridge_roles: int = 0
    new_jobs: int = 0
    updated_jobs: int = 0
    pursue_counts: dict[str, int] = field(default_factory=dict)
    # Per source family (e.g. "greenhouse", "himalayas"): raw results,
    # new jobs, parse errors, and failed queries with their error text.
    raw_by_source: dict[str, int] = field(default_factory=dict)
    new_by_source: dict[str, int] = field(default_factory=dict)
    parse_errors_by_source: dict[str, int] = field(default_factory=dict)
    failures_by_source: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class DiscoveryResult:
    metrics: DiscoveryMetrics = field(default_factory=DiscoveryMetrics)
    opportunities: list[DiscoveryOpportunity] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ai_refinement: Optional["AIRefinementStats"] = None  # set only when use_ai


def run_discovery(
    repository: JobRepository,
    sources: list[tuple[str, JobSource]],
    profiles: list[SearchProfile],
    *,
    remote_only: bool = False,
    employment_types: Optional[list[EmploymentType]] = None,
    min_fit: Optional[float] = None,
    limit: int = 20,
    use_ai: bool = True,
    career_profile: Optional[CareerProfile] = None,
    preferences: Optional[Preferences] = None,
    evidence_index: Optional[EvidenceIndex] = None,
    taxonomy: Optional[SkillsTaxonomy] = None,
    candidate: Optional[CandidateProfile] = None,
) -> DiscoveryResult:
    """Search every (source, profile-query) pair, score/match/rank the
    unique jobs discovered, and return the top `limit`.

    `sources` is `[(family_name, JobSource_instance), ...]` — family name
    is what "sources queried" counts (e.g. "creative_circle" once,
    "greenhouse" once regardless of how many boards), and what's compared
    against a `--source` filter. Each JobSource instance is queried once
    per profile query.

    Every job is scored and evaluated deterministically. `use_ai` never
    sends each job to Claude (Phase 4.3); it reviews only the top `limit`
    results, within `preferences.ai_refinement` — see
    ingestion/ai_refinement.py.
    """
    career_profile = career_profile or CareerProfile.load()
    preferences = preferences or Preferences.load()
    taxonomy = taxonomy or SkillsTaxonomy.load()
    candidate = candidate or CandidateProfile.load()

    result = DiscoveryResult()
    result.metrics.sources_queried = len({family for family, _ in sources})
    result.metrics.search_profiles = len(profiles)

    touched: dict[int, tuple[JobRecord, CareerFitResult]] = {}
    score_cache: dict[int, CareerFitResult] = {}

    for profile in profiles:
        for query_text in profile.queries:
            for family, source in sources:
                query = JobSearchQuery(keyword=query_text, remote_only=remote_only, page_size=50)
                try:
                    ingestion = run_search_ingestion(
                        source, query, repository,
                        profile=career_profile, preferences=preferences,
                        score=True, use_ai=False, evidence_index=evidence_index,
                        score_cache=score_cache,
                    )
                except Exception as exc:  # noqa: BLE001 - one bad (source, query) shouldn't kill the run
                    logger.error(
                        "discovery.query_failed",
                        extra={"family": family, "source": source.name, "profile": profile.name, "query": query_text},
                        exc_info=True,
                    )
                    kind = getattr(exc, "kind", type(exc).__name__)
                    result.notes.append(
                        f"'{profile.name}' query {query_text!r} against {source.name} failed ({kind}): {exc}"
                    )
                    result.metrics.failures_by_source.setdefault(family, []).append(f"{kind}: {exc}")
                    continue

                result.metrics.raw_jobs_discovered += ingestion.jobs_discovered
                m = result.metrics
                m.raw_by_source[family] = m.raw_by_source.get(family, 0) + ingestion.jobs_discovered
                m.new_by_source[family] = m.new_by_source.get(family, 0) + ingestion.jobs_new
                m.parse_errors_by_source[family] = m.parse_errors_by_source.get(family, 0) + ingestion.parser_errors
                result.metrics.new_jobs += ingestion.jobs_new
                result.metrics.updated_jobs += ingestion.jobs_updated

                if ingestion.jobs_discovered == 0:
                    result.notes.append(
                        f"'{profile.name}' query {query_text!r} against {source.name} found 0 jobs."
                    )

                for ranked in ingestion.ranked_jobs:
                    if ranked.fit is None:
                        continue
                    repository.record_search_profile_match(ranked.job.id, profile.name)
                    touched[ranked.job.id] = (ranked.job, ranked.fit)

    repository.commit()
    result.metrics.unique_jobs = len(touched)

    opportunities: list[DiscoveryOpportunity] = []
    for job, fit in touched.values():
        bridge = classify_bridge_role(job.title, job.description, taxonomy)
        immediate = assess_immediate_opportunity(fit)
        direction = assess_career_direction(fit)
        rank_score = compute_rank_score(
            fit, bridge, immediate, direction, preferences.discovery_ranking_weights
        )
        normalized = job_record_to_normalized(job)
        eval_result = evaluate_opportunity(
            normalized, fit, candidate=candidate, preferences=preferences,
            taxonomy=taxonomy, evidence_index=evidence_index, use_ai=False,
        )
        opportunities.append(
            DiscoveryOpportunity(
                job=job, fit=eval_result.fit, bridge=bridge, immediate=immediate, direction=direction,
                matched_profiles=list(job.discovered_by_profiles), rank_score=rank_score,
                decision=eval_result.decision,
            )
        )

    result.metrics.strong_matches = sum(1 for o in opportunities if o.fit.overall_fit >= 0.75)
    result.metrics.strong_bridge_roles = sum(
        1 for o in opportunities if o.bridge.classification == BridgeClassification.STRONG
    )
    pursue_counts: dict[str, int] = {}
    for o in opportunities:
        key = o.decision.pursue.recommendation.value
        pursue_counts[key] = pursue_counts.get(key, 0) + 1
    result.metrics.pursue_counts = pursue_counts

    if employment_types:
        opportunities = [o for o in opportunities if EmploymentType(o.job.employment_type) in employment_types]
    if min_fit is not None:
        opportunities = [o for o in opportunities if o.fit.overall_fit >= min_fit]

    opportunities.sort(key=lambda o: o.rank_score, reverse=True)
    if use_ai and opportunities:
        from careers_os.ingestion.ai_refinement import refine_finalists  # avoids an import cycle

        result.ai_refinement = refine_finalists(
            opportunities[:limit], repository=repository,
            budget=min(limit, preferences.ai_refinement.max_refinements_per_run),
            profile=career_profile, preferences=preferences, candidate=candidate,
            taxonomy=taxonomy, evidence_index=evidence_index,
        )
        opportunities.sort(key=lambda o: o.rank_score, reverse=True)
    result.opportunities = opportunities[:limit]
    return result
