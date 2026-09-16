import json
from pathlib import Path

import pytest

from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.sources.workable import parser

ACCOUNT = "exampleai"


@pytest.fixture
def response():
    path = Path(__file__).parent / "fixtures" / "workable" / "account_response.json"
    return json.loads(path.read_text())


def _job(response, shortcode: str) -> dict:
    return next(j for j in response["jobs"] if j.get("shortcode") == shortcode)


class TestSourceJobId:
    def test_round_trips(self):
        sid = parser.make_source_job_id("exampleai", "AAA111")
        assert sid == "exampleai:AAA111"
        assert parser.split_source_job_id(sid) == ("exampleai", "AAA111")

    def test_malformed_raises(self):
        with pytest.raises(ValueError):
            parser.split_source_job_id("nope")


class TestParse:
    def test_maps_core_fields_and_strips_html(self, response):
        raw = parser.parse_job_payload_to_raw_job(_job(response, "AAA111"), ACCOUNT, "Example AI")
        assert raw.source == "workable"
        assert raw.source_job_id == "exampleai:AAA111"
        assert raw.source_url == "https://apply.workable.com/j/AAA111"
        assert "<p>" not in raw.raw_description
        assert "Laravel platform architecture & API integrations" in raw.raw_description

    def test_missing_shortcode_raises(self, response):
        bad = next(j for j in response["jobs"] if "shortcode" not in j)
        with pytest.raises(ValueError):
            parser.parse_job_payload_to_raw_job(bad, ACCOUNT)


class TestRemoteStatus:
    def test_telecommuting_true_is_remote(self, response):
        assert parser.detect_remote_status(_job(response, "AAA111")) == RemoteStatus.REMOTE

    def test_telecommuting_false_is_unknown_not_onsite_or_remote(self, response):
        # false only means "not marked remote" — it can't distinguish
        # hybrid from onsite, so it must not be guessed as either.
        assert parser.detect_remote_status(_job(response, "CCC333")) == RemoteStatus.UNKNOWN


class TestLocation:
    def test_remote_location_is_suffixed_so_geography_check_can_see_it(self, response):
        assert parser.location_string(_job(response, "BBB222")) == "Paris, Île-de-France, France (Remote)"

    def test_non_remote_location_has_no_suffix(self, response):
        assert parser.location_string(_job(response, "CCC333")) == "Austin, Texas, United States"


class TestEmploymentType:
    def test_full_time(self, response):
        assert parser.detect_employment_type(_job(response, "AAA111")) == EmploymentType.FULL_TIME

    def test_contract(self, response):
        assert parser.detect_employment_type(_job(response, "CCC333")) == EmploymentType.CONTRACT


class TestNormalize:
    def test_end_to_end(self, response):
        raw = parser.parse_job_payload_to_raw_job(_job(response, "AAA111"), ACCOUNT, "Example AI")
        job = parser.normalize_raw_job(raw)
        assert job.source == "workable"
        assert job.company == "Example AI"
        assert job.remote_status == RemoteStatus.REMOTE
        assert job.salary_min == 180000.0
        assert job.salary_max == 220000.0
        assert job.posted_at is not None and job.posted_at.tzinfo is not None

    def test_company_falls_back_to_account_slug(self, response):
        raw = parser.parse_job_payload_to_raw_job(_job(response, "DDD444"), ACCOUNT, None)
        assert parser.normalize_raw_job(raw).company == ACCOUNT
