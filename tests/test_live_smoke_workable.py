"""Optional smoke test that hits the real Workable job-widget API.

Excluded from normal runs (pyproject.toml `addopts = "-m 'not live'"`).
Run explicitly with `pytest -m live`.
"""

import pytest

from careers_os.domain.query import JobSearchQuery
from careers_os.sources.workable.client import WorkableAccountNotFoundError
from careers_os.sources.workable.source import WorkableSource

pytestmark = pytest.mark.live

ACCOUNT = "huggingface"  # a real, active Workable account as of investigation


def test_live_search_returns_well_formed_jobs():
    with WorkableSource(ACCOUNT) as source:
        raw_jobs = source.search(JobSearchQuery(page_size=5))
        assert raw_jobs
        for raw in raw_jobs:
            job = source.normalize(raw)
            assert job.title
            assert job.source == "workable"
            assert job.source_url.startswith("https://apply.workable.com/")
            assert job.description  # details=true must actually return descriptions
        assert source.health().status.value in ("healthy", "degraded")


def test_live_fetch_job_matches_search_result():
    with WorkableSource(ACCOUNT) as source:
        raw_jobs = source.search(JobSearchQuery(page_size=1))
        assert raw_jobs
        assert source.fetch_job(raw_jobs[0].source_job_id).source_job_id == raw_jobs[0].source_job_id


def test_live_unknown_account_raises_not_found():
    with WorkableSource("this-account-should-not-exist-12345") as source:
        with pytest.raises(WorkableAccountNotFoundError):
            source.search(JobSearchQuery(page_size=1))
