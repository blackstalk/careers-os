import json
from pathlib import Path

import httpx
import pytest

from careers_os.domain.enums import EmploymentType, SourceHealthStatus
from careers_os.domain.query import JobSearchQuery, SortOrder
from careers_os.sources.ashby.client import AshbyBoardNotFoundError, AshbyClient
from careers_os.sources.ashby.source import AshbySource

FIXTURES = Path(__file__).parent / "fixtures" / "ashby"


@pytest.fixture
def jobs_list_response() -> dict:
    return json.loads((FIXTURES / "jobs_list_response.json").read_text())


def _transport_returning(payload: dict, status_code: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return httpx.MockTransport(handler)


def _source_with_payload(payload: dict, board_name: str = "exampleai") -> AshbySource:
    client = AshbyClient(transport=_transport_returning(payload), min_delay_seconds=0)
    return AshbySource(board_name, client=client)


class TestSearch:
    def test_returns_all_valid_jobs_when_no_filters(self, jobs_list_response):
        source = _source_with_payload(jobs_list_response)
        results = source.search(JobSearchQuery(page_size=50))
        # 5 entries in fixture, 1 missing 'id' -> dropped.
        assert len(results) == 4
        assert source.parse_failures == 1

    def test_keyword_filters_client_side_across_title_and_description(self, jobs_list_response):
        source = _source_with_payload(jobs_list_response)
        results = source.search(JobSearchQuery(keyword="solution architecture", page_size=50))
        assert len(results) == 1
        assert results[0].source_job_id == "exampleai:job-001"

    def test_remote_only_filters_via_workplace_type(self, jobs_list_response):
        source = _source_with_payload(jobs_list_response)
        results = source.search(JobSearchQuery(remote_only=True, page_size=50))
        ids = {r.source_job_id for r in results}
        assert ids == {"exampleai:job-002"}

    def test_employment_type_filters_client_side(self, jobs_list_response):
        source = _source_with_payload(jobs_list_response)
        results = source.search(JobSearchQuery(employment_type=EmploymentType.CONTRACT, page_size=50))
        ids = {r.source_job_id for r in results}
        assert ids == {"exampleai:job-003"}

    def test_posted_within_days_filters_client_side(self, jobs_list_response):
        source = _source_with_payload(jobs_list_response)
        results = source.search(JobSearchQuery(posted_within_days=30, page_size=50))
        ids = {r.source_job_id for r in results}
        assert "exampleai:job-003" not in ids

    def test_sort_by_date_orders_newest_first(self, jobs_list_response):
        source = _source_with_payload(jobs_list_response)
        results = source.search(JobSearchQuery(sort=SortOrder.DATE, page_size=50))
        dates = [r.raw_posted_date for r in results]
        assert dates == sorted(dates, reverse=True)

    def test_pagination_applies_client_side(self, jobs_list_response):
        source = _source_with_payload(jobs_list_response)
        page_1 = source.search(JobSearchQuery(page=1, page_size=2))
        page_2 = source.search(JobSearchQuery(page=2, page_size=2))
        assert len(page_1) == 2
        assert len(page_2) == 2
        assert {r.source_job_id for r in page_1}.isdisjoint({r.source_job_id for r in page_2})

    def test_board_not_found_raises_and_reports_broken_health(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="Not Found")

        client = AshbyClient(transport=httpx.MockTransport(handler), min_delay_seconds=0)
        source = AshbySource("does-not-exist", client=client)
        with pytest.raises(AshbyBoardNotFoundError):
            source.search(JobSearchQuery())
        assert source.health().status == SourceHealthStatus.BROKEN

    def test_zero_jobs_adds_a_note(self):
        source = _source_with_payload({"jobs": [], "apiVersion": 1})
        source.search(JobSearchQuery())
        assert any("zero jobs" in n for n in source.health().notes)

    def test_source_name_includes_board_name(self, jobs_list_response):
        source = _source_with_payload(jobs_list_response, board_name="openai")
        assert source.name == "ashby:openai"

    def test_normalized_job_source_is_constant_not_board_specific(self, jobs_list_response):
        source = _source_with_payload(jobs_list_response, board_name="openai")
        raw = source.search(JobSearchQuery(page_size=50))[0]
        job = source.normalize(raw)
        assert job.source == "ashby"


class TestFetchJob:
    def test_fetch_job_finds_matching_id_in_full_list(self, jobs_list_response):
        client = AshbyClient(transport=_transport_returning(jobs_list_response), min_delay_seconds=0)
        source = AshbySource("exampleai", client=client)
        raw = source.fetch_job("exampleai:job-001")
        assert raw.source_job_id == "exampleai:job-001"
        assert raw.raw_title == "Forward Deployed Engineer, Enterprise"

    def test_fetch_job_raises_when_id_not_in_list(self, jobs_list_response):
        from careers_os.sources.ashby.client import AshbyClientError

        client = AshbyClient(transport=_transport_returning(jobs_list_response), min_delay_seconds=0)
        source = AshbySource("exampleai", client=client)
        with pytest.raises(AshbyClientError):
            source.fetch_job("exampleai:does-not-exist")
