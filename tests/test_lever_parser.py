import json
from pathlib import Path

import pytest

from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.sources.lever import parser

COMPANY = "exampleai"


def _posting(postings_list_response, lever_id: str) -> dict:
    return next(p for p in postings_list_response if p.get("id") == lever_id)


@pytest.fixture
def postings_list_response():
    path = Path(__file__).parent / "fixtures" / "lever" / "postings_list_response.json"
    return json.loads(path.read_text())


class TestSourceJobId:
    def test_round_trips_company_and_id(self):
        source_job_id = parser.make_source_job_id("exampleai", "posting-001")
        assert source_job_id == "exampleai:posting-001"
        company, posting_id = parser.split_source_job_id(source_job_id)
        assert company == "exampleai"
        assert posting_id == "posting-001"

    def test_malformed_source_job_id_raises(self):
        with pytest.raises(ValueError):
            parser.split_source_job_id("not-a-valid-id")


class TestParseJobPayloadToRawJob:
    def test_maps_core_fields(self, postings_list_response):
        raw = parser.parse_job_payload_to_raw_job(_posting(postings_list_response, "posting-001"), COMPANY)
        assert raw.source == "lever"
        assert raw.source_job_id == "exampleai:posting-001"
        assert raw.source_url == "https://jobs.lever.co/exampleai/posting-001"
        assert raw.raw_title == "Forward Deployed Engineer, Enterprise"
        assert raw.raw_location == "San Francisco, CA"
        assert "Own solution architecture" in raw.raw_description

    def test_missing_id_raises(self, postings_list_response):
        bad_item = next(p for p in postings_list_response if "id" not in p)
        with pytest.raises(ValueError):
            parser.parse_job_payload_to_raw_job(bad_item, COMPANY)

    def test_created_at_epoch_ms_converted_to_iso(self, postings_list_response):
        raw = parser.parse_job_payload_to_raw_job(_posting(postings_list_response, "posting-001"), COMPANY)
        assert raw.raw_posted_date == "2026-09-10T13:00:00+00:00"


class TestRemoteStatusDetection:
    def test_workplace_type_hybrid(self, postings_list_response):
        payload = _posting(postings_list_response, "posting-001")
        assert parser.detect_remote_status(payload) == RemoteStatus.HYBRID

    def test_workplace_type_remote(self, postings_list_response):
        payload = _posting(postings_list_response, "posting-002")
        assert parser.detect_remote_status(payload) == RemoteStatus.REMOTE

    def test_location_text_fallback_when_no_workplace_type(self):
        payload = {"workplaceType": None, "categories": {"location": "Fully Remote, US"}}
        assert parser.detect_remote_status(payload) == RemoteStatus.REMOTE

    def test_unknown_when_neither_signal_present(self, postings_list_response):
        payload = _posting(postings_list_response, "posting-003")
        assert parser.detect_remote_status(payload) == RemoteStatus.UNKNOWN


class TestEmploymentTypeDetection:
    def test_commitment_text_maps_to_full_time(self, postings_list_response):
        payload = _posting(postings_list_response, "posting-001")
        assert parser.detect_employment_type(payload) == EmploymentType.FULL_TIME

    def test_commitment_text_maps_to_contract(self, postings_list_response):
        payload = _posting(postings_list_response, "posting-003")
        assert parser.detect_employment_type(payload) == EmploymentType.CONTRACT

    def test_unknown_when_absent(self):
        assert parser.detect_employment_type({}) == EmploymentType.UNKNOWN


class TestSalaryTextExtraction:
    def test_extracts_estimated_salary_range_pattern(self, postings_list_response):
        raw = parser.parse_job_payload_to_raw_job(_posting(postings_list_response, "posting-001"), COMPANY)
        low, high = parser.extract_salary_from_text(raw.raw_description)
        assert low == 180000.0
        assert high == 220000.0

    def test_no_match_returns_none_none(self, postings_list_response):
        raw = parser.parse_job_payload_to_raw_job(_posting(postings_list_response, "posting-004"), COMPANY)
        low, high = parser.extract_salary_from_text(raw.raw_description)
        assert low is None
        assert high is None


class TestNormalizeRawJob:
    def test_normalizes_end_to_end(self, postings_list_response):
        raw = parser.parse_job_payload_to_raw_job(_posting(postings_list_response, "posting-001"), COMPANY)
        job = parser.normalize_raw_job(raw)
        assert job.source == "lever"
        assert job.title == "Forward Deployed Engineer, Enterprise"
        assert job.company == COMPANY
        assert job.remote_status == RemoteStatus.HYBRID
        assert job.employment_type == EmploymentType.FULL_TIME
        assert job.salary_min == 180000.0
        assert job.salary_max == 220000.0
        assert job.currency == "USD"
        assert job.hourly_min is None and job.hourly_max is None
        # The combined salary-extraction text must never leak into the
        # normalized description shown to the user.
        assert "estimated salary range" not in (job.description or "").lower()
