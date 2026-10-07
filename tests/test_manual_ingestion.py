"""Phase 5: evaluating a posting supplied by hand (Problem 6).

No scraper. A URL plus copied description goes through the same
normalize -> persist -> score -> evaluate path as every adapter.
"""

from datetime import datetime, timezone

from careers_os.domain.enums import RemoteStatus
from careers_os.ingestion.manual import MANUAL_SOURCE, build_manual_job, ingest_manual_posting, posting_id
from careers_os.storage.repository import JobRepository

URL = "https://www.indeed.com/viewjob?jk=abc123"
BODY = ("Fully remote, United States. Requirements: 5+ years of PHP experience. "
        "Experience with Laravel and REST APIs. Salary $130,000 - $160,000 per year.")


class TestManualPosting:
    def test_url_derives_a_stable_id_so_re_adding_updates_in_place(self, db_session):
        repo = JobRepository(db_session)
        first, is_new = ingest_manual_posting(repo, *build_manual_job(
            url=URL, description=BODY, title="Senior Laravel Developer", company="Acme"))
        again, is_new_again = ingest_manual_posting(repo, *build_manual_job(
            url=URL, description=BODY + " Updated.", title="Senior Laravel Developer", company="Acme"))
        assert is_new and not is_new_again
        assert first.id == again.id
        assert again.source_job_id == posting_id(URL)

    def test_plain_facts_are_read_from_the_text(self):
        _, normalized = build_manual_job(url=URL, description=BODY, title="Senior Laravel Developer")
        assert normalized.remote_status == RemoteStatus.REMOTE
        assert (normalized.salary_min, normalized.salary_max) == (130000.0, 160000.0)
        assert normalized.source == MANUAL_SOURCE

    def test_absent_salary_stays_unknown_rather_than_guessed(self):
        _, normalized = build_manual_job(
            url=URL, description="Remote role building Laravel applications.", title="Developer")
        assert normalized.salary_min is None and normalized.salary_max is None

    def test_canonical_url_is_preserved_and_identifies_the_posting(self):
        canonical = "https://job-boards.greenhouse.io/acme/jobs/123"
        raw, normalized = build_manual_job(
            url=URL, description=BODY, title="Senior Laravel Developer", canonical_url=canonical)
        assert normalized.source_url == canonical
        assert raw.raw_payload["entered_url"] == URL
        assert normalized.source_job_id == posting_id(canonical)

    def test_human_status_survives_a_re_add(self, db_session):
        from careers_os.domain.enums import JobStatus
        repo = JobRepository(db_session)
        record, _ = ingest_manual_posting(repo, *build_manual_job(
            url=URL, description=BODY, title="Senior Laravel Developer"))
        repo.set_status(MANUAL_SOURCE, record.source_job_id, JobStatus.APPLIED)
        first_seen = record.first_seen_at
        again, _ = ingest_manual_posting(repo, *build_manual_job(
            url=URL, description=BODY + " More detail.", title="Senior Laravel Developer"))
        assert again.status == JobStatus.APPLIED.value
        assert again.first_seen_at == first_seen

    def test_a_manual_posting_is_flagged_against_the_employer_copy(self, db_session):
        """The existing cross-source duplicate scan links the two rather
        than merging them, so provenance survives."""
        from careers_os.domain.job import NormalizedJob
        from careers_os.domain.raw_job import RawJob
        repo = JobRepository(db_session)
        now = datetime.now(timezone.utc)
        ats = NormalizedJob(
            source="greenhouse", source_job_id="acme:123", source_url="https://boards.greenhouse.io/acme/123",
            title="Senior Laravel Developer", company="Acme", description=BODY, retrieved_at=now)
        repo.upsert_job(ats, RawJob(source="greenhouse", source_job_id="acme:123",
                                    source_url=ats.source_url, raw_payload={}, retrieved_at=now))
        record, _ = ingest_manual_posting(repo, *build_manual_job(
            url=URL, description=BODY, title="Senior Laravel Developer", company="Acme"))
        assert repo.duplicate_job_ids(record.id)
