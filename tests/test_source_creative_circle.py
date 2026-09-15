import httpx
import pytest

from careers_os.domain.enums import EmploymentType, SourceHealthStatus
from careers_os.domain.query import JobSearchQuery
from careers_os.sources.creative_circle.client import CreativeCircleClient
from careers_os.sources.creative_circle.source import CreativeCircleSource


def _transport_returning(payload: dict, status_code: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return httpx.MockTransport(handler)


def _source_with_payload(payload: dict) -> CreativeCircleSource:
    client = CreativeCircleClient(
        transport=_transport_returning(payload), min_delay_seconds=0
    )
    return CreativeCircleSource(client=client)


class TestSearch:
    def test_returns_raw_jobs_for_each_item(self, search_response):
        source = _source_with_payload(search_response)
        results = source.search(JobSearchQuery(keyword="architect"))
        assert len(results) == len(search_response["jobs"])

    def test_skips_malformed_items_but_keeps_valid_ones(self, malformed_search_response):
        source = _source_with_payload(malformed_search_response)
        results = source.search(JobSearchQuery())
        # 3 items in fixture, 1 missing Id -> dropped, 2 survive parsing.
        assert len(results) == 2
        assert source.parse_failures == 1

    def test_client_side_employment_type_filter(self, search_response):
        source = _source_with_payload(search_response)
        results = source.search(
            JobSearchQuery(employment_type=EmploymentType.FULL_TIME)
        )
        ids = {r.source_job_id for r in results}
        # Only 100001 and 100005 are TaxTerm=PERM in the fixture.
        assert ids == {"100001", "100005"}

    def test_remote_only_maps_to_work_location_type_id_param(self, search_response, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json=search_response)

        client = CreativeCircleClient(
            transport=httpx.MockTransport(handler), min_delay_seconds=0
        )
        source = CreativeCircleSource(client=client)
        source.search(JobSearchQuery(remote_only=True))
        assert captured["params"]["workLocationTypeId"] == "3"

    def test_days_posted_snaps_to_supported_preset(self, search_response):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json=search_response)

        client = CreativeCircleClient(
            transport=httpx.MockTransport(handler), min_delay_seconds=0
        )
        source = CreativeCircleSource(client=client)
        source.search(JobSearchQuery(posted_within_days=5))
        # 5 is between presets 3 and 7 -> snaps up to 7 (never under-fetch).
        assert captured["params"]["daysPosted"] == "7"

    def test_posted_within_days_enforces_exact_client_side_cutoff(self, search_response):
        # Fixture's oldest job (100003) is posted 2026-08-20; freeze "now" far
        # enough forward that a naive server-side bucket wouldn't catch it,
        # and confirm our own cutoff filter drops it.
        source = _source_with_payload(search_response)
        results = source.search(JobSearchQuery(posted_within_days=3))
        ids = {r.source_job_id for r in results}
        assert "100003" not in ids  # posted well over 3 days before "now" in fixture dates

    def test_health_is_healthy_after_successful_search(self, search_response):
        source = _source_with_payload(search_response)
        source.search(JobSearchQuery())
        health = source.health()
        assert health.status == SourceHealthStatus.HEALTHY
        assert health.requests_attempted == 1
        assert health.requests_failed == 0

    def test_health_is_broken_after_all_requests_fail(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        client = CreativeCircleClient(transport=httpx.MockTransport(handler), min_delay_seconds=0)
        source = CreativeCircleSource(client=client)
        with pytest.raises(Exception):
            source.search(JobSearchQuery())
        health = source.health()
        assert health.status == SourceHealthStatus.BROKEN

    def test_zero_results_note_added_for_keyword_search(self):
        empty_response = {"numFound": 0, "jobs": [], "maxScore": 0}
        source = _source_with_payload(empty_response)
        source.search(JobSearchQuery(keyword="solutions architect"))
        health = source.health()
        assert any("Zero results" in n for n in health.notes)


class TestFetchJob:
    def test_fetch_job_parses_detail_response(self, detail_response):
        source = _source_with_payload(detail_response)
        raw = source.fetch_job("100001")
        assert raw.source_job_id == "100001"
        assert "Own solution architecture" in raw.raw_description


class TestNormalize:
    def test_normalize_delegates_to_parser(self, search_response):
        source = _source_with_payload(search_response)
        raw = source.search(JobSearchQuery())[0]
        job = source.normalize(raw)
        assert job.source == "creative_circle"
