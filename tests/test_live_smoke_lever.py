"""Optional smoke test that hits the real Lever Postings API.

Excluded from normal runs (see pyproject.toml `addopts = "-m 'not live'"`).
Run explicitly with:

    pytest -m live
"""

import pytest

from careers_os.domain.query import JobSearchQuery
from careers_os.sources.lever.source import LeverSource

pytestmark = pytest.mark.live

COMPANY = "palantir"  # a real, currently-active Lever board as of investigation


def test_live_search_returns_well_formed_jobs():
    with LeverSource(COMPANY) as source:
        raw_jobs = source.search(JobSearchQuery(page_size=5))
        assert len(raw_jobs) > 0
        for raw in raw_jobs:
            job = source.normalize(raw)
            assert job.title
            assert job.source_url.startswith("https://jobs.lever.co/")
            assert job.source == "lever"
        health = source.health()
        assert health.status.value in ("healthy", "degraded")


def test_live_fetch_job_detail_matches_search_result():
    with LeverSource(COMPANY) as source:
        raw_jobs = source.search(JobSearchQuery(page_size=1))
        assert raw_jobs
        source_job_id = raw_jobs[0].source_job_id
        detail_raw = source.fetch_job(source_job_id)
        assert detail_raw.source_job_id == source_job_id


def test_live_unknown_company_raises_not_found():
    from careers_os.sources.lever.client import LeverCompanyNotFoundError

    with LeverSource("this-company-should-not-exist-12345") as source:
        with pytest.raises(LeverCompanyNotFoundError):
            source.search(JobSearchQuery(page_size=1))
