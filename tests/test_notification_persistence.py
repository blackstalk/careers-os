from careers_os.sources.creative_circle import parser
from careers_os.storage.repository import JobRepository


def _raw_and_normalized(search_response, source_job_id: str):
    item = next(j for j in search_response["jobs"] if j["Id"] == source_job_id)
    raw = parser.parse_job_payload_to_raw_job(item)
    return raw, parser.normalize_raw_job(raw)


class TestRecordNotification:
    def test_sent_notification_is_persisted(self, db_session, search_response):
        repo = JobRepository(db_session)
        raw, normalized = _raw_and_normalized(search_response, "100001")
        record, _ = repo.upsert_job(normalized, raw)

        repo.record_notification(
            record.id, evaluation_id=None, channel="email", operating_mode="passive",
            pursue_recommendation="strong_pursue", status="sent",
        )
        notifications = repo.list_notifications(record.id)
        assert len(notifications) == 1
        assert notifications[0].status == "sent"

    def test_failed_notification_is_persisted_with_error(self, db_session, search_response):
        repo = JobRepository(db_session)
        raw, normalized = _raw_and_normalized(search_response, "100001")
        record, _ = repo.upsert_job(normalized, raw)

        repo.record_notification(
            record.id, evaluation_id=None, channel="email", operating_mode="passive",
            pursue_recommendation="strong_pursue", status="failed", error="SMTP timeout",
        )
        failed = repo.list_notifications(record.id, status="failed")
        assert len(failed) == 1
        assert failed[0].error == "SMTP timeout"

    def test_list_notifications_filters_by_status(self, db_session, search_response):
        repo = JobRepository(db_session)
        raw, normalized = _raw_and_normalized(search_response, "100001")
        record, _ = repo.upsert_job(normalized, raw)

        repo.record_notification(
            record.id, evaluation_id=None, channel="email", operating_mode="passive",
            pursue_recommendation="strong_pursue", status="sent",
        )
        repo.record_notification(
            record.id, evaluation_id=None, channel="email", operating_mode="passive",
            pursue_recommendation="strong_pursue", status="failed", error="boom",
        )
        assert len(repo.list_notifications(record.id)) == 2
        assert len(repo.list_notifications(record.id, status="sent")) == 1
        assert len(repo.list_notifications(record.id, status="failed")) == 1

    def test_notifications_scoped_to_their_own_job(self, db_session, search_response):
        repo = JobRepository(db_session)
        raw1, normalized1 = _raw_and_normalized(search_response, "100001")
        raw2, normalized2 = _raw_and_normalized(search_response, "100002")
        record1, _ = repo.upsert_job(normalized1, raw1)
        record2, _ = repo.upsert_job(normalized2, raw2)

        repo.record_notification(
            record1.id, evaluation_id=None, channel="email", operating_mode="passive",
            pursue_recommendation="strong_pursue", status="sent",
        )
        assert len(repo.list_notifications(record1.id)) == 1
        assert len(repo.list_notifications(record2.id)) == 0
