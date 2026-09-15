import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from careers_os.domain.enums import JobStatus
from careers_os.domain.job import NormalizedJob
from careers_os.domain.raw_job import RawJob
from careers_os.domain.scoring import CareerFitResult
from careers_os.storage.dedup import MIN_CONFIDENCE_TO_FLAG, compare_jobs
from careers_os.storage.db import (
    JobChangeRecord,
    JobRecord,
    JobScoreRecord,
    PossibleDuplicateRecord,
    RawJobRecord,
)

logger = logging.getLogger("careers_os.storage")


def _comp_summary(salary_min, salary_max, hourly_min, hourly_max) -> str:
    if salary_min or salary_max:
        return f"${salary_min or '?'}-{salary_max or '?'}/yr"
    if hourly_min or hourly_max:
        return f"${hourly_min or '?'}-{hourly_max or '?'}/hr"
    return "(none published)"


class JobRepository:
    """Persists raw/normalized jobs and scores.

    Deduplication key is `(source, source_job_id)` (see docs/architecture.md
    §Deduplication). `upsert_job` is the one place that matters for the
    "human review state survives re-sync" requirement: everything about a
    job gets refreshed from the source except `status`.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def _find(self, source: str, source_job_id: str) -> Optional[JobRecord]:
        stmt = select(JobRecord).where(
            JobRecord.source == source, JobRecord.source_job_id == source_job_id
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_job(self, source: str, source_job_id: str) -> Optional[JobRecord]:
        return self._find(source, source_job_id)

    def upsert_job(self, normalized: NormalizedJob, raw: RawJob) -> tuple[JobRecord, bool]:
        """Insert or refresh a job. Returns (record, is_new).

        Never touches `status`, `first_seen_at`, or the row's identity.
        """
        now = datetime.now(timezone.utc)

        raw_record = RawJobRecord(
            source=raw.source,
            source_job_id=raw.source_job_id,
            source_url=raw.source_url,
            raw_title=raw.raw_title,
            raw_location=raw.raw_location,
            raw_description=raw.raw_description,
            raw_compensation=raw.raw_compensation,
            raw_employment_type=raw.raw_employment_type,
            raw_posted_date=raw.raw_posted_date,
            raw_updated_date=raw.raw_updated_date,
            raw_recruiter=raw.raw_recruiter,
            raw_payload=raw.raw_payload,
            retrieved_at=raw.retrieved_at,
        )
        self.session.add(raw_record)
        self.session.flush()  # assign raw_record.id

        existing = self._find(normalized.source, normalized.source_job_id)
        is_new = existing is None

        if existing is None:
            existing = JobRecord(
                source=normalized.source,
                source_job_id=normalized.source_job_id,
                status=JobStatus.NEW.value,
                first_seen_at=now,
            )
            self.session.add(existing)
            logger.info(
                "repository.job_created",
                extra={"source": normalized.source, "source_job_id": normalized.source_job_id},
            )
        else:
            logger.debug(
                "repository.job_updated",
                extra={"source": normalized.source, "source_job_id": normalized.source_job_id},
            )
            self._record_changes(existing, normalized, now)

        # Refresh every field derived from the source. Deliberately excluded:
        # status, first_seen_at, id — those belong to the human workflow.
        existing.source_url = normalized.source_url
        existing.title = normalized.title
        existing.company = normalized.company
        existing.location = normalized.location
        existing.remote_status = normalized.remote_status.value
        existing.employment_type = normalized.employment_type.value
        existing.salary_min = normalized.salary_min
        existing.salary_max = normalized.salary_max
        existing.hourly_min = normalized.hourly_min
        existing.hourly_max = normalized.hourly_max
        existing.currency = normalized.currency
        existing.description = normalized.description
        existing.skills = normalized.skills
        existing.technologies = normalized.technologies
        existing.posted_at = normalized.posted_at
        existing.updated_at = normalized.updated_at
        existing.retrieved_at = normalized.retrieved_at
        existing.recruiter_name = normalized.recruiter_name
        existing.recruiter_contact = normalized.recruiter_contact
        existing.last_seen_at = now
        existing.source_updated_at = normalized.updated_at
        existing.raw_job_id = raw_record.id

        self.session.flush()

        if is_new:
            self._scan_for_cross_source_duplicates(existing, now)

        return existing, is_new

    def _record_changes(self, existing: JobRecord, normalized: NormalizedJob, now: datetime) -> None:
        """Append-only change log for a re-synced job. Only the fields
        called out in docs/architecture.md#change-tracking are tracked;
        this never marks anything closed — see storage/dedup.py's sibling
        comment about not inferring closure from a missing search hit.
        """
        if (existing.description or None) != (normalized.description or None):
            self.session.add(
                JobChangeRecord(
                    job_id=existing.id, detected_at=now, field="description",
                    old_value=(existing.description or "")[:200] or None,
                    new_value=(normalized.description or "")[:200] or None,
                )
            )
        if (existing.location or None) != (normalized.location or None):
            self.session.add(
                JobChangeRecord(
                    job_id=existing.id, detected_at=now, field="location",
                    old_value=existing.location, new_value=normalized.location,
                )
            )
        old_comp = _comp_summary(existing.salary_min, existing.salary_max, existing.hourly_min, existing.hourly_max)
        new_comp = _comp_summary(normalized.salary_min, normalized.salary_max, normalized.hourly_min, normalized.hourly_max)
        if old_comp != new_comp:
            self.session.add(
                JobChangeRecord(
                    job_id=existing.id, detected_at=now, field="compensation",
                    old_value=old_comp, new_value=new_comp,
                )
            )

    def _scan_for_cross_source_duplicates(self, new_job: JobRecord, now: datetime) -> None:
        """Flag (never merge) possible duplicates against jobs from OTHER
        sources. Only run once, at first discovery — see storage/dedup.py.
        """
        candidates = self.session.execute(
            select(JobRecord).where(JobRecord.source != new_job.source, JobRecord.id != new_job.id)
        ).scalars().all()

        for other in candidates:
            signal = compare_jobs(
                new_job.title, new_job.company, new_job.location, new_job.description,
                other.title, other.company, other.location, other.description,
            )
            if signal.confidence < MIN_CONFIDENCE_TO_FLAG:
                continue
            self.session.add(
                PossibleDuplicateRecord(
                    job_a_id=new_job.id,
                    job_b_id=other.id,
                    confidence=signal.confidence,
                    matched_signals=signal.matched,
                    detected_at=now,
                )
            )
            logger.info(
                "repository.possible_duplicate_flagged",
                extra={
                    "job_a": f"{new_job.source}/{new_job.source_job_id}",
                    "job_b": f"{other.source}/{other.source_job_id}",
                    "confidence": signal.confidence,
                    "matched": signal.matched,
                },
            )
        self.session.flush()

    def record_search_profile_match(self, job_id: int, profile_name: str) -> None:
        """Discovery provenance (Phase 2.5, see docs/discovery.md): note
        that `profile_name` turned up this job, without duplicating the
        job row if another profile already found it this run or a prior one.
        """
        record = self.session.get(JobRecord, job_id)
        if record is None:
            raise ValueError(f"No job found with id={job_id}")
        if profile_name not in record.discovered_by_profiles:
            record.discovered_by_profiles = [*record.discovered_by_profiles, profile_name]
            self.session.flush()

    def list_duplicates(self) -> list[PossibleDuplicateRecord]:
        return list(self.session.execute(select(PossibleDuplicateRecord)).scalars().all())

    def list_changes(self, job_id: int) -> list[JobChangeRecord]:
        stmt = select(JobChangeRecord).where(JobChangeRecord.job_id == job_id).order_by(
            JobChangeRecord.detected_at.desc()
        )
        return list(self.session.execute(stmt).scalars().all())

    def set_status(self, source: str, source_job_id: str, status: JobStatus) -> JobRecord:
        record = self._find(source, source_job_id)
        if record is None:
            raise ValueError(f"No job found for {source}/{source_job_id}")
        record.status = status.value
        self.session.flush()
        return record

    def save_score(self, job_id: int, result: CareerFitResult) -> JobScoreRecord:
        score = JobScoreRecord(
            job_id=job_id,
            computed_at=datetime.now(timezone.utc),
            scorer_version=result.scorer_version,
            overall_fit=result.overall_fit,
            ai_evaluation_included=result.ai_evaluation_included,
            components={k: v.model_dump() for k, v in result.components().items()},
            experience_detail=(
                result.experience_detail.model_dump(mode="json")
                if result.experience_detail is not None
                else None
            ),
        )
        self.session.add(score)
        self.session.flush()
        return score

    def list_jobs(
        self,
        *,
        source: Optional[str] = None,
        status: Optional[JobStatus] = None,
        limit: int = 50,
    ) -> list[JobRecord]:
        stmt = select(JobRecord).order_by(JobRecord.last_seen_at.desc()).limit(limit)
        if source:
            stmt = stmt.where(JobRecord.source == source)
        if status:
            stmt = stmt.where(JobRecord.status == status.value)
        return list(self.session.execute(stmt).scalars().all())

    def commit(self) -> None:
        self.session.commit()
