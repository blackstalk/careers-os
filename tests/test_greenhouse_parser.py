import pytest

from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.sources.greenhouse import parser

BOARD = "exampleai"


def _job(jobs_list_response, greenhouse_id: int) -> dict:
    return next(j for j in jobs_list_response["jobs"] if j.get("id") == greenhouse_id)


@pytest.fixture
def jobs_list_response():
    import json
    from pathlib import Path

    path = Path(__file__).parent / "fixtures" / "greenhouse" / "jobs_list_response.json"
    return json.loads(path.read_text())


@pytest.fixture
def job_detail_response():
    import json
    from pathlib import Path

    path = Path(__file__).parent / "fixtures" / "greenhouse" / "job_detail_response.json"
    return json.loads(path.read_text())


class TestHtmlToText:
    def test_double_escaped_html_is_fully_unescaped_and_stripped(self):
        raw = "&lt;div&gt;&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;&lt;/div&gt;"
        assert parser.html_to_text(raw) == "Hello & welcome"

    def test_none_input_returns_none(self):
        assert parser.html_to_text(None) is None


class TestSourceJobId:
    def test_round_trips_board_and_id(self):
        source_job_id = parser.make_source_job_id("exampleai", 5000001)
        assert source_job_id == "exampleai:5000001"
        board, job_id = parser.split_source_job_id(source_job_id)
        assert board == "exampleai"
        assert job_id == "5000001"

    def test_malformed_source_job_id_raises(self):
        with pytest.raises(ValueError):
            parser.split_source_job_id("not-a-valid-id")


class TestParseJobPayloadToRawJob:
    def test_maps_core_fields(self, jobs_list_response):
        raw = parser.parse_job_payload_to_raw_job(_job(jobs_list_response, 5000001), BOARD)
        assert raw.source == "greenhouse"
        assert raw.source_job_id == "exampleai:5000001"
        assert raw.source_url.endswith("/5000001")
        assert raw.raw_title == "Forward Deployed Engineer, Enterprise"
        assert raw.raw_location == "San Francisco, CA"
        assert "Own solution architecture" in raw.raw_description

    def test_missing_id_raises(self, jobs_list_response):
        bad_item = next(j for j in jobs_list_response["jobs"] if "id" not in j)
        with pytest.raises(ValueError):
            parser.parse_job_payload_to_raw_job(bad_item, BOARD)


class TestRemoteStatusDetection:
    def test_metadata_takes_precedence_over_misleading_location_text(self, jobs_list_response):
        # Real-world case discovered in Anthropic's live board: location text
        # says "Remote-Friendly" but the structured metadata says "Hybrid".
        payload = _job(jobs_list_response, 5000001)
        assert parser.detect_remote_status(payload) == RemoteStatus.HYBRID

    def test_location_text_fallback_when_no_metadata(self, jobs_list_response):
        payload = _job(jobs_list_response, 5000002)
        assert parser.detect_remote_status(payload) == RemoteStatus.REMOTE

    def test_unknown_when_neither_metadata_nor_text_indicates_remote(self, jobs_list_response):
        payload = _job(jobs_list_response, 5000004)
        assert parser.detect_remote_status(payload) == RemoteStatus.UNKNOWN


class TestEmploymentTypeDetection:
    def test_metadata_label_maps_to_employment_type(self, jobs_list_response):
        payload = _job(jobs_list_response, 5000003)
        assert parser.detect_employment_type(payload) == EmploymentType.CONTRACT

    def test_unknown_when_no_signal_present(self, jobs_list_response):
        payload = _job(jobs_list_response, 5000002)
        assert parser.detect_employment_type(payload) == EmploymentType.UNKNOWN


class TestSalaryTextExtraction:
    def test_extracts_annual_salary_boilerplate_pattern(self, jobs_list_response):
        raw = parser.parse_job_payload_to_raw_job(_job(jobs_list_response, 5000001), BOARD)
        low, high = parser.extract_salary_from_text(raw.raw_description)
        assert low == 180000.0
        assert high == 220000.0

    def test_no_match_returns_none_none(self, jobs_list_response):
        raw = parser.parse_job_payload_to_raw_job(_job(jobs_list_response, 5000004), BOARD)
        low, high = parser.extract_salary_from_text(raw.raw_description)
        assert low is None
        assert high is None


class TestNormalizeRawJob:
    def test_normalizes_end_to_end(self, jobs_list_response):
        raw = parser.parse_job_payload_to_raw_job(_job(jobs_list_response, 5000001), BOARD)
        job = parser.normalize_raw_job(raw)
        assert job.source == "greenhouse"
        assert job.title == "Forward Deployed Engineer, Enterprise"
        assert job.remote_status == RemoteStatus.HYBRID
        assert job.salary_min == 180000.0
        assert job.salary_max == 220000.0
        assert job.currency == "USD"
        assert job.hourly_min is None and job.hourly_max is None

    def test_company_falls_back_to_board_token_when_absent(self, jobs_list_response):
        payload = dict(_job(jobs_list_response, 5000001))
        payload.pop("company_name")
        raw = parser.parse_job_payload_to_raw_job(payload, BOARD)
        job = parser.normalize_raw_job(raw)
        assert job.company == BOARD

    def test_detail_response_parses_identically_to_list_item(self, job_detail_response):
        raw = parser.parse_job_payload_to_raw_job(job_detail_response, BOARD)
        job = parser.normalize_raw_job(raw)
        assert job.title == "Forward Deployed Engineer, Enterprise"
        assert job.salary_min == 180000.0
