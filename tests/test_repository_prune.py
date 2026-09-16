from datetime import datetime, timezone

from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.job import NormalizedJob
from careers_os.domain.raw_job import RawJob
from careers_os.domain.scoring import CareerFitResult, FitComponent
from careers_os.storage.db import JobScoreRecord, RawJobRecord
from careers_os.storage.repository import JobRepository

NOW = datetime.now(timezone.utc)


def _raw(source_job_id: str, title: str) -> RawJob:
    return RawJob(
        source="ashby", source_job_id=source_job_id, source_url=f"https://x/{source_job_id}",
        raw_title=title, raw_payload={}, retrieved_at=NOW,
    )


def _normalized(source_job_id: str, title: str) -> NormalizedJob:
    return NormalizedJob(
        source="ashby", source_job_id=source_job_id, source_url=f"https://x/{source_job_id}",
        title=title, remote_status=RemoteStatus.UNKNOWN, employment_type=EmploymentType.UNKNOWN,
        retrieved_at=NOW,
    )


def _fit(score: float = 0.5) -> CareerFitResult:
    component = FitComponent(score=score, reason="r", confidence=0.5)
    return CareerFitResult(
        role_fit=component, technical_fit=component, career_direction_fit=component,
        compensation_fit=component, work_arrangement_fit=component, experience_fit=component,
        overall_fit=score, scorer_version="test",
    )


class TestPruneRawJobHistory:
    def test_collapses_repeated_upserts_of_the_same_job_to_one_row(self, db_session):
        repo = JobRepository(db_session)
        for _ in range(5):
            repo.upsert_job(_normalized("job-1", "Applied AI Engineer"), _raw("job-1", "Applied AI Engineer"))
        db_session.commit()

        assert db_session.query(RawJobRecord).count() == 5
        stats = repo.prune_history()
        assert stats["raw_jobs_deleted"] == 4
        assert db_session.query(RawJobRecord).count() == 1

    def test_keeps_one_row_per_distinct_job(self, db_session):
        repo = JobRepository(db_session)
        for _ in range(3):
            repo.upsert_job(_normalized("job-1", "Applied AI Engineer"), _raw("job-1", "Applied AI Engineer"))
        for _ in range(2):
            repo.upsert_job(_normalized("job-2", "Solutions Architect"), _raw("job-2", "Solutions Architect"))
        db_session.commit()

        repo.prune_history()
        remaining = db_session.query(RawJobRecord).all()
        assert {r.source_job_id for r in remaining} == {"job-1", "job-2"}
        assert len(remaining) == 2

    def test_retains_the_most_recently_inserted_row(self, db_session):
        repo = JobRepository(db_session)
        repo.upsert_job(_normalized("job-1", "First Title"), _raw("job-1", "First Title"))
        repo.upsert_job(_normalized("job-1", "Second Title"), _raw("job-1", "Second Title"))
        db_session.commit()

        repo.prune_history()
        remaining = db_session.query(RawJobRecord).one()
        assert remaining.raw_title == "Second Title"


class TestPruneScoreHistory:
    def test_collapses_repeated_scores_for_the_same_job_to_one_row(self, db_session):
        repo = JobRepository(db_session)
        record, _ = repo.upsert_job(_normalized("job-1", "Applied AI Engineer"), _raw("job-1", "Applied AI Engineer"))
        for score in [0.3, 0.5, 0.9]:
            repo.save_score(record.id, _fit(score))
        db_session.commit()

        assert db_session.query(JobScoreRecord).count() == 3
        stats = repo.prune_history()
        assert stats["job_scores_deleted"] == 2
        remaining = db_session.query(JobScoreRecord).one()
        assert remaining.overall_fit == 0.9

    def test_does_not_collapse_scores_across_different_jobs(self, db_session):
        repo = JobRepository(db_session)
        record_1, _ = repo.upsert_job(_normalized("job-1", "A"), _raw("job-1", "A"))
        record_2, _ = repo.upsert_job(_normalized("job-2", "B"), _raw("job-2", "B"))
        repo.save_score(record_1.id, _fit(0.5))
        repo.save_score(record_2.id, _fit(0.6))
        db_session.commit()

        repo.prune_history()
        assert db_session.query(JobScoreRecord).count() == 2


class TestPruneDoesNotTouchOtherState:
    def test_jobs_table_is_unaffected(self, db_session):
        repo = JobRepository(db_session)
        for _ in range(4):
            repo.upsert_job(_normalized("job-1", "Applied AI Engineer"), _raw("job-1", "Applied AI Engineer"))
        db_session.commit()

        repo.prune_history()
        assert len(repo.list_jobs(limit=100)) == 1

    def test_notification_records_are_unaffected(self, db_session):
        repo = JobRepository(db_session)
        record, _ = repo.upsert_job(_normalized("job-1", "Applied AI Engineer"), _raw("job-1", "Applied AI Engineer"))
        db_session.commit()
        repo.record_notification(
            record.id, evaluation_id=None, channel="email",
            operating_mode="passive", pursue_recommendation="strong_pursue", status="sent",
        )
        db_session.commit()

        repo.prune_history()
        assert len(repo.list_notifications(record.id, status="sent")) == 1
