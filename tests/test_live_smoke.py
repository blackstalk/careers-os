"""Optional smoke test that hits the real Creative Circle API.

Excluded from normal runs (see pyproject.toml `addopts = "-m 'not live'"`).
Run explicitly with:

    pytest -m live
"""

import pytest

from careers_os.domain.query import JobSearchQuery
from careers_os.sources.creative_circle.source import CreativeCircleSource

pytestmark = pytest.mark.live


def test_live_search_returns_well_formed_jobs():
    with CreativeCircleSource() as source:
        raw_jobs = source.search(JobSearchQuery(page_size=5))
        assert len(raw_jobs) > 0
        for raw in raw_jobs:
            job = source.normalize(raw)
            assert job.title
            assert job.source_url.startswith("https://candidateportal.creativecircle.com/job-detail/")
        health = source.health()
        assert health.status.value in ("healthy", "degraded")


def test_live_fetch_job_detail_matches_search_result():
    with CreativeCircleSource() as source:
        raw_jobs = source.search(JobSearchQuery(page_size=1))
        assert raw_jobs
        source_job_id = raw_jobs[0].source_job_id
        detail_raw = source.fetch_job(source_job_id)
        assert detail_raw.source_job_id == source_job_id
