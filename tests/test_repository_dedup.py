from careers_os.domain.enums import JobStatus
from careers_os.sources.creative_circle import parser
from careers_os.storage.repository import JobRepository


def _raw_and_normalized(search_response, source_job_id: str):
    item = next(j for j in search_response["jobs"] if j["Id"] == source_job_id)
    raw = parser.parse_job_payload_to_raw_job(item)
    return raw, parser.normalize_raw_job(raw)


class TestUpsertJob:
    def test_first_upsert_creates_new_record_with_status_new(self, db_session, search_response):
        repo = JobRepository(db_session)
        raw, normalized = _raw_and_normalized(search_response, "100001")

        record, is_new = repo.upsert_job(normalized, raw)

        assert is_new is True
        assert record.status == JobStatus.NEW.value
        assert record.first_seen_at == record.last_seen_at

    def test_second_upsert_of_same_source_job_id_updates_not_duplicates(
        self, db_session, search_response
    ):
        repo = JobRepository(db_session)
        raw, normalized = _raw_and_normalized(search_response, "100001")

        repo.upsert_job(normalized, raw)
        record_2, is_new_2 = repo.upsert_job(normalized, raw)

        assert is_new_2 is False
        all_jobs = repo.list_jobs(limit=100)
        assert len(all_jobs) == 1
        assert all_jobs[0].id == record_2.id

    def test_upsert_preserves_human_set_status_across_resync(self, db_session, search_response):
        repo = JobRepository(db_session)
        raw, normalized = _raw_and_normalized(search_response, "100001")

        repo.upsert_job(normalized, raw)
        repo.set_status("creative_circle", "100001", JobStatus.SAVED)

        # Simulate a re-sync bringing back fresh (but unchanged) data.
        record, is_new = repo.upsert_job(normalized, raw)

        assert is_new is False
        assert record.status == JobStatus.SAVED.value

    def test_upsert_refreshes_source_data_fields(self, db_session, search_response):
        repo = JobRepository(db_session)
        raw, normalized = _raw_and_normalized(search_response, "100001")
        repo.upsert_job(normalized, raw)

        normalized.title = "Updated Title After Recruiter Edit"
        record, _ = repo.upsert_job(normalized, raw)

        assert record.title == "Updated Title After Recruiter Edit"

    def test_upsert_advances_last_seen_at_but_not_first_seen_at(self, db_session, search_response):
        repo = JobRepository(db_session)
        raw, normalized = _raw_and_normalized(search_response, "100001")
        first_record, _ = repo.upsert_job(normalized, raw)
        first_seen = first_record.first_seen_at

        second_record, _ = repo.upsert_job(normalized, raw)

        assert second_record.first_seen_at == first_seen
        assert second_record.last_seen_at >= first_seen

    def test_different_source_job_ids_create_separate_records(self, db_session, search_response):
        repo = JobRepository(db_session)
        for job_id in ("100001", "100002"):
            raw, normalized = _raw_and_normalized(search_response, job_id)
            repo.upsert_job(normalized, raw)

        assert len(repo.list_jobs(limit=100)) == 2

    def test_set_status_on_unknown_job_raises(self, db_session):
        repo = JobRepository(db_session)
        try:
            repo.set_status("creative_circle", "does-not-exist", JobStatus.SAVED)
            assert False, "expected ValueError"
        except ValueError:
            pass

    def test_list_jobs_filters_by_status(self, db_session, search_response):
        repo = JobRepository(db_session)
        for job_id in ("100001", "100002"):
            raw, normalized = _raw_and_normalized(search_response, job_id)
            repo.upsert_job(normalized, raw)
        repo.set_status("creative_circle", "100001", JobStatus.SAVED)

        saved = repo.list_jobs(status=JobStatus.SAVED)
        assert len(saved) == 1
        assert saved[0].source_job_id == "100001"


class TestCrossSourceDuplicateDetection:
    def test_same_source_jobs_are_never_flagged_against_each_other(self, db_session, search_response):
        repo = JobRepository(db_session)
        for job_id in ("100001", "100002"):
            raw, normalized = _raw_and_normalized(search_response, job_id)
            repo.upsert_job(normalized, raw)
        assert repo.list_duplicates() == []

    def test_cross_source_match_on_title_and_company_is_flagged(self, db_session, search_response):
        repo = JobRepository(db_session)
        raw, normalized = _raw_and_normalized(search_response, "100001")
        record_a, _ = repo.upsert_job(normalized, raw)

        # Same title/company/location, but from a different source entirely.
        other = normalized.model_copy(
            update={"source": "greenhouse", "source_job_id": "board:999"}
        )
        other_raw = raw.model_copy(update={"source": "greenhouse", "source_job_id": "board:999"})
        record_b, _ = repo.upsert_job(other, other_raw)

        flags = repo.list_duplicates()
        assert len(flags) == 1
        assert {flags[0].job_a_id, flags[0].job_b_id} == {record_a.id, record_b.id}
        assert flags[0].confidence >= 0.6

    def test_duplicate_scan_only_runs_once_at_first_discovery(self, db_session, search_response):
        repo = JobRepository(db_session)
        raw, normalized = _raw_and_normalized(search_response, "100001")
        repo.upsert_job(normalized, raw)

        other = normalized.model_copy(update={"source": "greenhouse", "source_job_id": "board:999"})
        other_raw = raw.model_copy(update={"source": "greenhouse", "source_job_id": "board:999"})
        repo.upsert_job(other, other_raw)
        assert len(repo.list_duplicates()) == 1

        # Re-syncing the same job again must not create duplicate flag rows.
        repo.upsert_job(other, other_raw)
        assert len(repo.list_duplicates()) == 1


class TestChangeTracking:
    def test_no_changes_recorded_on_first_insert(self, db_session, search_response):
        repo = JobRepository(db_session)
        raw, normalized = _raw_and_normalized(search_response, "100001")
        record, _ = repo.upsert_job(normalized, raw)
        assert repo.list_changes(record.id) == []

    def test_description_change_is_recorded_on_resync(self, db_session, search_response):
        repo = JobRepository(db_session)
        raw, normalized = _raw_and_normalized(search_response, "100001")
        record, _ = repo.upsert_job(normalized, raw)

        updated = normalized.model_copy(update={"description": "A completely different description."})
        repo.upsert_job(updated, raw)

        changes = repo.list_changes(record.id)
        fields_changed = {c.field for c in changes}
        assert "description" in fields_changed

    def test_compensation_change_is_recorded_on_resync(self, db_session, search_response):
        repo = JobRepository(db_session)
        raw, normalized = _raw_and_normalized(search_response, "100001")
        record, _ = repo.upsert_job(normalized, raw)

        updated = normalized.model_copy(update={"salary_min": 999999, "salary_max": 1000000})
        repo.upsert_job(updated, raw)

        changes = repo.list_changes(record.id)
        fields_changed = {c.field for c in changes}
        assert "compensation" in fields_changed

    def test_unchanged_resync_records_nothing(self, db_session, search_response):
        repo = JobRepository(db_session)
        raw, normalized = _raw_and_normalized(search_response, "100001")
        record, _ = repo.upsert_job(normalized, raw)
        repo.upsert_job(normalized, raw)  # identical re-sync
        assert repo.list_changes(record.id) == []
