import pytest

from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.sources.creative_circle import parser


def _job(search_response, source_job_id: str) -> dict:
    return next(j for j in search_response["jobs"] if j["Id"] == source_job_id)


class TestParseJobPayloadToRawJob:
    def test_search_item_maps_core_fields(self, search_response):
        raw = parser.parse_job_payload_to_raw_job(_job(search_response, "100001"))
        assert raw.source == "creative_circle"
        assert raw.source_job_id == "100001"
        assert raw.source_url.endswith("/job-detail/100001")
        assert raw.raw_title == "Solutions Architect, Client Integrations"
        assert raw.raw_location == "Austin, TX"
        assert raw.raw_employment_type == "PERM"
        assert raw.raw_recruiter == "Jordan Lee"
        assert "salary 175000-210000" in raw.raw_compensation

    def test_missing_id_raises(self, malformed_search_response):
        bad_item = malformed_search_response["jobs"][0]
        with pytest.raises(ValueError):
            parser.parse_job_payload_to_raw_job(bad_item)

    def test_empty_city_state_lists_yield_no_location(self, search_response):
        raw = parser.parse_job_payload_to_raw_job(_job(search_response, "100003"))
        assert raw.raw_location is None

    def test_detail_response_maps_via_camelcase_fields(self, detail_response):
        raw = parser.parse_job_payload_to_raw_job(detail_response)
        assert raw.source_job_id == "100001"
        assert raw.raw_location == "Austin, TX"
        assert raw.raw_updated_date == "2026-09-11T08:00:00.000Z"
        assert "full-text" not in (raw.raw_description or "")  # sanity: no leakage of markers
        assert "Own solution architecture" in raw.raw_description


class TestNormalizeRawJob:
    def test_salary_only(self, search_response):
        raw = parser.parse_job_payload_to_raw_job(_job(search_response, "100001"))
        job = parser.normalize_raw_job(raw)
        assert job.salary_min == 175000
        assert job.salary_max == 210000
        assert job.hourly_min is None
        assert job.hourly_max is None
        assert job.currency == "USD"

    def test_hourly_only(self, search_response):
        raw = parser.parse_job_payload_to_raw_job(_job(search_response, "100002"))
        job = parser.normalize_raw_job(raw)
        assert job.hourly_min == 45
        assert job.hourly_max == 60
        assert job.salary_min is None
        assert job.salary_max is None

    def test_both_present_are_not_cross_converted(self, search_response):
        raw = parser.parse_job_payload_to_raw_job(_job(search_response, "100004"))
        job = parser.normalize_raw_job(raw)
        assert job.salary_min == 55000
        assert job.salary_max == 62000
        assert job.hourly_min == 26
        assert job.hourly_max == 30

    def test_neither_present(self, search_response):
        raw = parser.parse_job_payload_to_raw_job(_job(search_response, "100003"))
        job = parser.normalize_raw_job(raw)
        assert job.salary_min is None
        assert job.salary_max is None
        assert job.hourly_min is None
        assert job.hourly_max is None
        assert job.currency is None

    @pytest.mark.parametrize(
        "source_job_id,expected",
        [("100001", RemoteStatus.REMOTE), ("100002", RemoteStatus.HYBRID), ("100004", RemoteStatus.ONSITE)],
    )
    def test_remote_status_mapping(self, search_response, source_job_id, expected):
        raw = parser.parse_job_payload_to_raw_job(_job(search_response, source_job_id))
        job = parser.normalize_raw_job(raw)
        assert job.remote_status == expected

    def test_unknown_work_location_type_id_maps_to_unknown(self, malformed_search_response):
        item = malformed_search_response["jobs"][1]
        raw = parser.parse_job_payload_to_raw_job(item)
        job = parser.normalize_raw_job(raw)
        assert job.remote_status == RemoteStatus.UNKNOWN

    @pytest.mark.parametrize(
        "source_job_id,expected",
        [("100001", EmploymentType.FULL_TIME), ("100002", EmploymentType.FREELANCE)],
    )
    def test_employment_type_mapping(self, search_response, source_job_id, expected):
        raw = parser.parse_job_payload_to_raw_job(_job(search_response, source_job_id))
        job = parser.normalize_raw_job(raw)
        assert job.employment_type == expected

    def test_unrecognized_tax_term_maps_to_unknown(self, malformed_search_response):
        item = malformed_search_response["jobs"][1]
        raw = parser.parse_job_payload_to_raw_job(item)
        job = parser.normalize_raw_job(raw)
        assert job.employment_type == EmploymentType.UNKNOWN

    def test_null_title_falls_back_to_placeholder(self, malformed_search_response):
        item = malformed_search_response["jobs"][1]
        raw = parser.parse_job_payload_to_raw_job(item)
        job = parser.normalize_raw_job(raw)
        assert job.title == "(untitled)"

    def test_unparseable_date_is_none_not_an_exception(self, malformed_search_response):
        item = malformed_search_response["jobs"][1]
        raw = parser.parse_job_payload_to_raw_job(item)
        job = parser.normalize_raw_job(raw)
        assert job.posted_at is None

    def test_date_parsing_handles_z_suffix(self, search_response):
        raw = parser.parse_job_payload_to_raw_job(_job(search_response, "100001"))
        job = parser.normalize_raw_job(raw)
        assert job.posted_at is not None
        assert job.posted_at.year == 2026
        assert job.posted_at.month == 9

    def test_skills_from_tags_list(self, search_response):
        raw = parser.parse_job_payload_to_raw_job(_job(search_response, "100005"))
        job = parser.normalize_raw_job(raw)
        assert "Machine Learning" in job.skills
        assert "APIs" in job.skills

    def test_skills_from_tags_split_on_detail_response(self, detail_response):
        raw = parser.parse_job_payload_to_raw_job(detail_response)
        job = parser.normalize_raw_job(raw)
        assert "API Integration" in job.skills
        assert "Cloud Architecture" in job.skills

    def test_company_name_absent_on_detail_response(self, detail_response):
        # Known limitation: the detail endpoint does not return company name.
        # See docs/sources/creative-circle.md.
        raw = parser.parse_job_payload_to_raw_job(detail_response)
        job = parser.normalize_raw_job(raw)
        assert job.company is None

    def test_company_name_present_on_search_response(self, search_response):
        raw = parser.parse_job_payload_to_raw_job(_job(search_response, "100001"))
        job = parser.normalize_raw_job(raw)
        assert job.company == "Acme Retail Co"
