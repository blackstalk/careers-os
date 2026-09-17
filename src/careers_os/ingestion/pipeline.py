import logging
from dataclasses import dataclass, field

from pydantic import BaseModel

from careers_os.career.preferences import Preferences
from careers_os.career.profile import CareerProfile
from careers_os.domain.query import JobSearchQuery
from careers_os.domain.scoring import CareerFitResult
from careers_os.scoring.engine import score_job
from careers_os.career.evidence_index import EvidenceIndex
from careers_os.sources.base import JobSource
from careers_os.storage.db import JobRecord
from careers_os.storage.repository import JobRepository

logger = logging.getLogger("careers_os.ingestion")


class RankedJob(BaseModel):
    job: JobRecord
    fit: CareerFitResult | None

    model_config = {"arbitrary_types_allowed": True}


@dataclass
class IngestionResult:
    source: str
    jobs_discovered: int = 0
    jobs_new: int = 0
    jobs_updated: int = 0
    jobs_skipped: int = 0
    parser_errors: int = 0
    ranked_jobs: list[RankedJob] = field(default_factory=list)


def run_search_ingestion(
    source: JobSource,
    query: JobSearchQuery,
    repository: JobRepository,
    *,
    profile: CareerProfile | None = None,
    preferences: Preferences | None = None,
    score: bool = True,
    use_ai: bool = True,
    evidence_index: EvidenceIndex | None = None,
    score_cache: dict[int, CareerFitResult] | None = None,
) -> IngestionResult:
    """Run one search → normalize → dedup/persist → score pass.

    This is the one place that's allowed to know about all of: a source
    adapter, the repository, and the scoring engine — everything else stays
    decoupled (see docs/architecture.md's pipeline diagram).
    """
    result = IngestionResult(source=source.name)

    try:
        raw_jobs = source.search(query)
    except Exception:
        logger.error("ingestion.search_failed", extra={"source": source.name}, exc_info=True)
        raise

    result.jobs_discovered = len(raw_jobs)

    if score and (profile is None or preferences is None):
        profile = profile or CareerProfile.load()
        preferences = preferences or Preferences.load()

    for raw in raw_jobs:
        try:
            normalized = source.normalize(raw)
        except Exception:
            result.parser_errors += 1
            logger.error(
                "ingestion.normalize_failed",
                extra={"source": source.name, "source_job_id": raw.source_job_id},
                exc_info=True,
            )
            continue

        record, is_new = repository.upsert_job(normalized, raw)
        if is_new:
            result.jobs_new += 1
        else:
            result.jobs_updated += 1

        fit_result = None
        if score and score_cache is not None and record.id in score_cache:
            # Same job already scored earlier in this run (found by another
            # query) — skip the repeat scoring and its AI call.
            fit_result = score_cache[record.id]
        elif score:
            normalized.id = record.id
            fit_result = score_job(
                normalized, profile, preferences, use_ai=use_ai, evidence_index=evidence_index
            )
            repository.save_score(record.id, fit_result)
            if score_cache is not None:
                score_cache[record.id] = fit_result

        result.ranked_jobs.append(RankedJob(job=record, fit=fit_result))

    repository.commit()

    if score:
        result.ranked_jobs.sort(
            key=lambda r: r.fit.overall_fit if r.fit else 0.0, reverse=True
        )

    logger.info(
        "ingestion.completed",
        extra={
            "source": source.name,
            "jobs_discovered": result.jobs_discovered,
            "jobs_new": result.jobs_new,
            "jobs_updated": result.jobs_updated,
            "jobs_skipped": result.jobs_skipped,
            "parser_errors": result.parser_errors,
        },
    )
    return result
