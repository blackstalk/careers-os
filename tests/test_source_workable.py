import json
from pathlib import Path

import httpx
import pytest

from careers_os.domain.enums import EmploymentType, SourceHealthStatus
from careers_os.domain.query import JobSearchQuery, SortOrder
from careers_os.sources.workable.client import (
    WorkableAccountNotFoundError,
    WorkableClient,
    WorkableClientError,
)
from careers_os.sources.workable.source import WorkableSource

FIXTURES = Path(__file__).parent / "fixtures" / "workable"


@pytest.fixture
def response() -> dict:
    return json.loads((FIXTURES / "account_response.json").read_text())


def _source(payload: dict, account: str = "exampleai", captured: list | None = None) -> WorkableSource:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        return httpx.Response(200, json=payload)

    client = WorkableClient(transport=httpx.MockTransport(handler), min_delay_seconds=0)
    return WorkableSource(account, client=client)


class TestSearch:
    def test_returns_valid_jobs_and_counts_parse_failures(self, response):
        source = _source(response)
        results = source.search(JobSearchQuery(page_size=50))
        assert len(results) == 4
        assert source.parse_failures == 1

    def test_requests_details_so_descriptions_are_included(self, response):
        captured: list = []
        _source(response, captured=captured).search(JobSearchQuery())
        assert captured[0].url.params.get("details") == "true"
        assert captured[0].url.path == "/api/v1/widget/accounts/exampleai"

    def test_keyword_filters_client_side(self, response):
        results = _source(response).search(JobSearchQuery(keyword="laravel", page_size=50))
        assert [r.source_job_id for r in results] == ["exampleai:AAA111"]

    def test_remote_only_uses_telecommuting_flag(self, response):
        results = _source(response).search(JobSearchQuery(remote_only=True, page_size=50))
        assert {r.source_job_id for r in results} == {"exampleai:AAA111", "exampleai:BBB222"}

    def test_employment_type_filter(self, response):
        results = _source(response).search(JobSearchQuery(employment_type=EmploymentType.CONTRACT, page_size=50))
        assert {r.source_job_id for r in results} == {"exampleai:CCC333"}

    def test_posted_within_days_filter(self, response):
        results = _source(response).search(JobSearchQuery(posted_within_days=30, page_size=50))
        assert "exampleai:CCC333" not in {r.source_job_id for r in results}

    def test_sort_by_date(self, response):
        results = _source(response).search(JobSearchQuery(sort=SortOrder.DATE, page_size=50))
        dates = [r.raw_posted_date for r in results]
        assert dates == sorted(dates, reverse=True)

    def test_pagination(self, response):
        source = _source(response)
        p1 = source.search(JobSearchQuery(page=1, page_size=2))
        p2 = source.search(JobSearchQuery(page=2, page_size=2))
        assert len(p1) == 2 and len(p2) == 2
        assert {r.source_job_id for r in p1}.isdisjoint({r.source_job_id for r in p2})

    def test_account_not_found_is_broken_health(self):
        client = WorkableClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(404, text="Not Found")), min_delay_seconds=0
        )
        source = WorkableSource("nope", client=client)
        with pytest.raises(WorkableAccountNotFoundError):
            source.search(JobSearchQuery())
        assert source.health().status == SourceHealthStatus.BROKEN

    def test_zero_jobs_adds_note(self):
        source = _source({"name": "X", "jobs": []})
        source.search(JobSearchQuery())
        assert any("zero jobs" in n for n in source.health().notes)

    def test_normalized_source_is_constant(self, response):
        source = _source(response, account="laravel")
        assert source.name == "workable:laravel"
        assert source.normalize(source.search(JobSearchQuery(page_size=1))[0]).source == "workable"


class TestFetchJob:
    def test_finds_job_by_relisting(self, response):
        raw = _source(response).fetch_job("exampleai:BBB222")
        assert raw.raw_title == "Machine Learning Engineer - EMEA Remote"

    def test_missing_job_raises(self, response):
        with pytest.raises(WorkableClientError):
            _source(response).fetch_job("exampleai:ZZZ999")
