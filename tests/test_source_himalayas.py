import json
from pathlib import Path

import httpx
import pytest

from careers_os.career.candidate import CandidateProfile
from careers_os.career.eligibility import evaluate_eligibility
from careers_os.domain.eligibility import EligibilityStatus
from careers_os.domain.enums import EmploymentType, RemoteStatus, SourceHealthStatus
from careers_os.domain.query import JobSearchQuery
from careers_os.sources.himalayas import parser
from careers_os.sources.himalayas.client import (
    HimalayasClient,
    HimalayasClientError,
    HimalayasRateLimitedError,
    HimalayasSchemaError,
)
from careers_os.sources.himalayas.source import HimalayasSource

PAGE = json.loads((Path(__file__).parent / "fixtures" / "himalayas" / "search_page.json").read_text())
EMPTY = {**PAGE, "totalCount": 0, "jobs": []}


def _source(handler, pages=2):
    client = HimalayasClient(transport=httpx.MockTransport(handler), min_delay_seconds=0)
    return HimalayasSource(client=client, pages_per_query=pages)


def _paged(request):
    page = int(request.url.params.get("page", "1"))
    return httpx.Response(200, json=PAGE if page == 1 else EMPTY)


class TestParser:
    def test_source_job_id_is_company_and_slug(self):
        assert parser.source_job_id(PAGE["jobs"][0]) == "acme-remote:senior-laravel-engineer-111"

    def test_missing_guid_raises(self):
        with pytest.raises(ValueError):
            parser.source_job_id(PAGE["jobs"][4])

    def test_normalizes_us_restricted_full_time_job(self):
        job = parser.normalize_raw_job(parser.parse_job_payload_to_raw_job(PAGE["jobs"][0]))
        assert job.source == "himalayas"
        assert job.company == "Acme Remote"
        assert job.remote_status == RemoteStatus.REMOTE
        assert job.location == "United States - Remote"
        assert job.employment_type == EmploymentType.FULL_TIME
        assert (job.salary_min, job.salary_max) == (150000.0, 190000.0)
        assert "Laravel platform & REST APIs" in job.description
        assert job.posted_at is not None

    def test_hourly_contract_pay_is_not_treated_as_salary(self):
        job = parser.normalize_raw_job(parser.parse_job_payload_to_raw_job(PAGE["jobs"][1]))
        assert job.employment_type == EmploymentType.CONTRACT
        assert (job.hourly_min, job.hourly_max) == (90.0, 120.0)
        assert job.salary_min is None
        assert job.location == "Remote (Worldwide)"


class TestSearch:
    def test_sends_keyword_us_filter_and_recent_sort(self):
        seen = []

        def handler(request):
            seen.append(request)
            return _paged(request)

        _source(handler).search(JobSearchQuery(keyword="laravel"))
        params = seen[0].url.params
        assert (params["q"], params["country"], params["sort"], params["page"]) == ("laravel", "US", "recent", "1")

    def test_dedupes_repeated_guid_and_counts_parse_failures(self):
        source = _source(_paged)
        results = source.search(JobSearchQuery(keyword="engineer"))
        assert [r.source_job_id for r in results] == [
            "acme-remote:senior-laravel-engineer-111",
            "globex-ai:applied-ai-engineer-222",
            "initech:backend-engineer-333",
        ]
        assert source.parse_failures == 1

    def test_stops_at_page_cap(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=PAGE)

        _source(handler, pages=2).search(JobSearchQuery(keyword="engineer"))
        assert len(calls) == 2

    def test_stops_early_on_empty_page(self):
        calls = []

        def handler(request):
            calls.append(request)
            return _paged(request)

        _source(handler, pages=5).search(JobSearchQuery(keyword="engineer"))
        assert len(calls) == 2

    def test_employment_type_filter(self):
        results = _source(_paged).search(JobSearchQuery(keyword="x", employment_type=EmploymentType.CONTRACT))
        assert [r.source_job_id for r in results] == ["globex-ai:applied-ai-engineer-222"]

    def test_healthy_zero_results_is_noted_not_an_error(self):
        source = _source(lambda r: httpx.Response(200, json=EMPTY))
        assert source.search(JobSearchQuery(keyword="nothing")) == []
        health = source.health()
        assert health.status == SourceHealthStatus.HEALTHY
        assert any("zero US-open matches" in n for n in health.notes)


class TestHealthAndErrors:
    def test_rate_limit_is_reported_as_its_own_kind(self, monkeypatch):
        monkeypatch.setattr("tenacity.nap.time.sleep", lambda s: None)
        source = _source(lambda r: httpx.Response(429))
        with pytest.raises(HimalayasRateLimitedError):
            source.search(JobSearchQuery(keyword="x"))
        health = source.health()
        assert health.status == SourceHealthStatus.BROKEN
        assert "last error kind: rate_limited" in health.notes

    def test_changed_response_shape_fails_loudly_instead_of_returning_zero(self):
        source = _source(lambda r: httpx.Response(200, json={"results": []}))
        with pytest.raises(HimalayasSchemaError):
            source.search(JobSearchQuery(keyword="x"))
        assert "last error kind: schema_changed" in source.health().notes

    def test_non_json_response_is_a_schema_error(self):
        source = _source(lambda r: httpx.Response(200, text="<html>maintenance</html>"))
        with pytest.raises(HimalayasSchemaError):
            source.search(JobSearchQuery(keyword="x"))

    def test_server_error_is_a_client_error(self):
        source = _source(lambda r: httpx.Response(500))
        with pytest.raises(HimalayasClientError):
            source.search(JobSearchQuery(keyword="x"))


class TestEligibilityOfAggregatorLocations:
    def _eligibility(self, idx):
        job = parser.normalize_raw_job(parser.parse_job_payload_to_raw_job(PAGE["jobs"][idx]))
        return evaluate_eligibility(job, CandidateProfile.load()).status

    def test_us_restricted_remote_is_eligible(self):
        assert self._eligibility(0) == EligibilityStatus.ELIGIBLE

    def test_worldwide_remote_is_eligible(self):
        assert self._eligibility(1) == EligibilityStatus.ELIGIBLE

    def test_non_us_restricted_remote_needs_verification(self):
        assert self._eligibility(3) == EligibilityStatus.VERIFY

    def test_multi_country_list_including_us_is_eligible(self):
        payload = {**PAGE["jobs"][3], "locationRestrictions": ["Canada", "United States", "Germany"]}
        job = parser.normalize_raw_job(parser.parse_job_payload_to_raw_job(payload))
        assert evaluate_eligibility(job, CandidateProfile.load()).status == EligibilityStatus.ELIGIBLE
