"""Discovery orchestration (Phase 2.5).

Runs a set of search profiles against a set of sources, reusing the
existing Phase 1/2 pipeline (`run_search_ingestion`) for every individual
(source, query) call — this module adds *ranking and provenance* on top,
it does not reimplement search/normalize/score. See docs/discovery.md.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from careers_os.career.bridge_role import BridgeClassification, BridgeRoleResult, classify_bridge_role
from careers_os.career.evidence_index import EvidenceIndex
from careers_os.career.opportunity_value import (
    OpportunityAssessment,
    assess_career_direction,
    assess_immediate_opportunity,
)
from careers_os.career.discovery_ranking import compute_rank_score
from careers_os.career.preferences import Preferences
from careers_os.career.profile import CareerProfile
from careers_os.career.search_profiles import SearchProfile
from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.enums import EmploymentType
from careers_os.domain.query import JobSearchQuery
from careers_os.domain.scoring import CareerFitResult
from careers_os.ingestion.pipeline import run_search_ingestion
from careers_os.sources.base import JobSource
from careers_os.storage.db import JobRecord
from careers_os.storage.repository import JobRepository

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


@dataclass
class DiscoveryResult:
    metrics: DiscoveryMetrics = field(default_factory=DiscoveryMetrics)
    opportunities: list[DiscoveryOpportunity] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


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
) -> DiscoveryResult:
    """Search every (source, profile-query) pair, score/match/rank the
    unique jobs discovered, and return the top `limit`.

    `sources` is `[(family_name, JobSource_instance), ...]` — family name
    is what "sources queried" counts (e.g. "creative_circle" once,
    "greenhouse" once regardless of how many boards), and what's compared
    against a `--source` filter. Each JobSource instance is queried once
    per profile query.
    """
    career_profile = career_profile or CareerProfile.load()
    preferences = preferences or Preferences.load()
    taxonomy = taxonomy or SkillsTaxonomy.load()

    result = DiscoveryResult()
    result.metrics.sources_queried = len({family for family, _ in sources})
    result.metrics.search_profiles = len(profiles)

    touched: dict[int, tuple[JobRecord, CareerFitResult]] = {}

    for profile in profiles:
        for query_text in profile.queries:
            for family, source in sources:
                query = JobSearchQuery(keyword=query_text, remote_only=remote_only, page_size=50)
                try:
                    ingestion = run_search_ingestion(
                        source, query, repository,
                        profile=career_profile, preferences=preferences,
                        score=True, use_ai=use_ai, evidence_index=evidence_index,
                    )
                except Exception as exc:  # noqa: BLE001 - one bad (source, query) shouldn't kill the run
                    logger.error(
                        "discovery.query_failed",
                        extra={"family": family, "source": source.name, "profile": profile.name, "query": query_text},
                        exc_info=True,
                    )
                    result.notes.append(
                        f"'{profile.name}' query {query_text!r} against {source.name} failed: {exc}"
                    )
                    continue

                result.metrics.raw_jobs_discovered += ingestion.jobs_discovered
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
        opportunities.append(
            DiscoveryOpportunity(
                job=job, fit=fit, bridge=bridge, immediate=immediate, direction=direction,
                matched_profiles=list(job.discovered_by_profiles), rank_score=rank_score,
            )
        )

    result.metrics.strong_matches = sum(1 for o in opportunities if o.fit.overall_fit >= 0.75)
    result.metrics.strong_bridge_roles = sum(
        1 for o in opportunities if o.bridge.classification == BridgeClassification.STRONG
    )

    if employment_types:
        opportunities = [o for o in opportunities if EmploymentType(o.job.employment_type) in employment_types]
    if min_fit is not None:
        opportunities = [o for o in opportunities if o.fit.overall_fit >= min_fit]

    opportunities.sort(key=lambda o: o.rank_score, reverse=True)
    result.opportunities = opportunities[:limit]
    return result
