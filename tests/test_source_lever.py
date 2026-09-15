import json
from pathlib import Path

import httpx
import pytest

from careers_os.domain.enums import EmploymentType, SourceHealthStatus
from careers_os.domain.query import JobSearchQuery, SortOrder
from careers_os.sources.lever.client import LeverClient, LeverCompanyNotFoundError
from careers_os.sources.lever.source import LeverSource

FIXTURES = Path(__file__).parent / "fixtures" / "lever"


@pytest.fixture
def postings_list_response() -> list:
    return json.loads((FIXTURES / "postings_list_response.json").read_text())


def _transport_returning(payload, status_code: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return httpx.MockTransport(handler)


def _source_with_payload(payload, company: str = "exampleai") -> LeverSource:
    client = LeverClient(transport=_transport_returning(payload), min_delay_seconds=0)
    return LeverSource(company, client=client)


class TestSearch:
    def test_returns_all_valid_jobs_when_no_filters(self, postings_list_response):
        source = _source_with_payload(postings_list_response)
        results = source.search(JobSearchQuery(page_size=50))
        # 5 entries in fixture, 1 missing 'id' -> dropped.
        assert len(results) == 4
        assert source.parse_failures == 1

    def test_keyword_filters_client_side_across_title_and_description(self, postings_list_response):
        source = _source_with_payload(postings_list_response)
        results = source.search(JobSearchQuery(keyword="solution architecture", page_size=50))
        assert len(results) == 1
        assert results[0].source_job_id == "exampleai:posting-001"

    def test_remote_only_filters_via_workplace_type(self, postings_list_response):
        source = _source_with_payload(postings_list_response)
        results = source.search(JobSearchQuery(remote_only=True, page_size=50))
        ids = {r.source_job_id for r in results}
        assert ids == {"exampleai:posting-002"}

    def test_employment_type_filters_client_side(self, postings_list_response):
        source = _source_with_payload(postings_list_response)
        results = source.search(JobSearchQuery(employment_type=EmploymentType.CONTRACT, page_size=50))
        ids = {r.source_job_id for r in results}
        assert ids == {"exampleai:posting-003"}

    def test_posted_within_days_filters_client_side(self, postings_list_response):
        source = _source_with_payload(postings_list_response)
        results = source.search(JobSearchQuery(posted_within_days=30, page_size=50))
        ids = {r.source_job_id for r in results}
        assert "exampleai:posting-003" not in ids

    def test_sort_by_date_orders_newest_first(self, postings_list_response):
        source = _source_with_payload(postings_list_response)
        results = source.search(JobSearchQuery(sort=SortOrder.DATE, page_size=50))
        dates = [r.raw_posted_date for r in results]
        assert dates == sorted(dates, reverse=True)

    def test_pagination_applies_client_side(self, postings_list_response):
        source = _source_with_payload(postings_list_response)
        page_1 = source.search(JobSearchQuery(page=1, page_size=2))
        page_2 = source.search(JobSearchQuery(page=2, page_size=2))
        assert len(page_1) == 2
        assert len(page_2) == 2
        assert {r.source_job_id for r in page_1}.isdisjoint({r.source_job_id for r in page_2})

    def test_company_not_found_raises_and_reports_broken_health(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"ok": False, "error": "Document not found"})

        client = LeverClient(transport=httpx.MockTransport(handler), min_delay_seconds=0)
        source = LeverSource("does-not-exist", client=client)
        with pytest.raises(LeverCompanyNotFoundError):
            source.search(JobSearchQuery())
        assert source.health().status == SourceHealthStatus.BROKEN

    def test_zero_jobs_adds_a_note(self):
        source = _source_with_payload([])
        source.search(JobSearchQuery())
        assert any("zero jobs" in n for n in source.health().notes)

    def test_source_name_includes_company(self, postings_list_response):
        source = _source_with_payload(postings_list_response, company="palantir")
        assert source.name == "lever:palantir"

    def test_normalized_job_source_is_constant_not_company_specific(self, postings_list_response):
        source = _source_with_payload(postings_list_response, company="palantir")
        raw = source.search(JobSearchQuery(page_size=50))[0]
        job = source.normalize(raw)
        assert job.source == "lever"


class TestFetchJob:
    def test_fetch_job_uses_company_from_source_job_id(self, postings_list_response):
        captured_paths = []
        detail = next(p for p in postings_list_response if p.get("id") == "posting-001")

        def handler(request: httpx.Request) -> httpx.Response:
            captured_paths.append(str(request.url.path))
            return httpx.Response(200, json=detail)

        client = LeverClient(transport=httpx.MockTransport(handler), min_delay_seconds=0)
        source = LeverSource("exampleai", client=client)
        raw = source.fetch_job("exampleai:posting-001")
        assert raw.source_job_id == "exampleai:posting-001"
        assert captured_paths == ["/v0/postings/exampleai/posting-001"]
