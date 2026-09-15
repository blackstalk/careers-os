import json
from pathlib import Path

import pytest

from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.sources.ashby import parser

BOARD = "exampleai"


def _job(jobs_list_response, ashby_id: str) -> dict:
    return next(j for j in jobs_list_response["jobs"] if j.get("id") == ashby_id)


@pytest.fixture
def jobs_list_response():
    path = Path(__file__).parent / "fixtures" / "ashby" / "jobs_list_response.json"
    return json.loads(path.read_text())


class TestSourceJobId:
    def test_round_trips_board_and_id(self):
        source_job_id = parser.make_source_job_id("exampleai", "job-001")
        assert source_job_id == "exampleai:job-001"
        board, job_id = parser.split_source_job_id(source_job_id)
        assert board == "exampleai"
        assert job_id == "job-001"

    def test_malformed_source_job_id_raises(self):
        with pytest.raises(ValueError):
            parser.split_source_job_id("not-a-valid-id")


class TestParseJobPayloadToRawJob:
    def test_maps_core_fields(self, jobs_list_response):
        raw = parser.parse_job_payload_to_raw_job(_job(jobs_list_response, "job-001"), BOARD)
        assert raw.source == "ashby"
        assert raw.source_job_id == "exampleai:job-001"
        assert raw.source_url == "https://jobs.ashbyhq.com/exampleai/job-001"
        assert raw.raw_title == "Forward Deployed Engineer, Enterprise"
        assert raw.raw_location == "San Francisco"
        assert raw.raw_description == "Own solution architecture for enterprise customers."

    def test_missing_id_raises(self, jobs_list_response):
        bad_item = next(j for j in jobs_list_response["jobs"] if "id" not in j)
        with pytest.raises(ValueError):
            parser.parse_job_payload_to_raw_job(bad_item, BOARD)


class TestRemoteStatusDetection:
    def test_workplace_type_hybrid(self, jobs_list_response):
        payload = _job(jobs_list_response, "job-001")
        assert parser.detect_remote_status(payload) == RemoteStatus.HYBRID

    def test_workplace_type_remote(self, jobs_list_response):
        payload = _job(jobs_list_response, "job-002")
        assert parser.detect_remote_status(payload) == RemoteStatus.REMOTE

    def test_falls_back_to_is_remote_when_workplace_type_missing(self):
        payload = {"workplaceType": None, "isRemote": True}
        assert parser.detect_remote_status(payload) == RemoteStatus.REMOTE

    def test_unknown_when_neither_signal_present(self, jobs_list_response):
        payload = _job(jobs_list_response, "job-003")
        assert parser.detect_remote_status(payload) == RemoteStatus.UNKNOWN


class TestEmploymentTypeDetection:
    def test_structured_full_time(self, jobs_list_response):
        payload = _job(jobs_list_response, "job-001")
        assert parser.detect_employment_type(payload) == EmploymentType.FULL_TIME

    def test_structured_contract(self, jobs_list_response):
        payload = _job(jobs_list_response, "job-003")
        assert parser.detect_employment_type(payload) == EmploymentType.CONTRACT

    def test_unknown_when_absent(self):
        assert parser.detect_employment_type({}) == EmploymentType.UNKNOWN


class TestSalaryExtraction:
    def test_extracts_structured_salary_tier(self, jobs_list_response):
        payload = _job(jobs_list_response, "job-001")
        low, high = parser.extract_salary_range(payload)
        assert low == 180000.0
        assert high == 220000.0

    def test_no_compensation_returns_none_none(self, jobs_list_response):
        payload = _job(jobs_list_response, "job-002")
        low, high = parser.extract_salary_range(payload)
        assert low is None
        assert high is None

    def test_multiple_tiers_take_min_of_mins_and_max_of_maxes(self):
        payload = {
            "compensation": {
                "compensationTiers": [
                    {"components": [{"compensationType": "Salary", "minValue": 150000, "maxValue": 200000}]},
                    {"components": [{"compensationType": "Salary", "minValue": 130000, "maxValue": 190000}]},
                ]
            }
        }
        low, high = parser.extract_salary_range(payload)
        assert low == 130000.0
        assert high == 200000.0

    def test_non_salary_components_are_ignored(self):
        payload = {
            "compensation": {
                "compensationTiers": [
                    {"components": [{"compensationType": "Commission", "minValue": 1, "maxValue": 2}]},
                ]
            }
        }
        low, high = parser.extract_salary_range(payload)
        assert low is None
        assert high is None


class TestNormalizeRawJob:
    def test_normalizes_end_to_end(self, jobs_list_response):
        raw = parser.parse_job_payload_to_raw_job(_job(jobs_list_response, "job-001"), BOARD)
        job = parser.normalize_raw_job(raw)
        assert job.source == "ashby"
        assert job.title == "Forward Deployed Engineer, Enterprise"
        assert job.company == BOARD
        assert job.remote_status == RemoteStatus.HYBRID
        assert job.employment_type == EmploymentType.FULL_TIME
        assert job.salary_min == 180000.0
        assert job.salary_max == 220000.0
        assert job.currency == "USD"
        assert job.hourly_min is None and job.hourly_max is None
