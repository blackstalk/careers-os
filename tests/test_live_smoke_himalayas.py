"""Optional smoke test against the real Himalayas API (`pytest -m live`)."""

import pytest

from careers_os.domain.query import JobSearchQuery
from careers_os.sources.himalayas.source import HimalayasSource

pytestmark = pytest.mark.live


def test_live_search_returns_well_formed_remote_jobs():
    with HimalayasSource(pages_per_query=1) as source:
        raw_jobs = source.search(JobSearchQuery(keyword="backend engineer"))
        assert raw_jobs, "Himalayas returned nothing for a very common query — API change?"
        for raw in raw_jobs[:5]:
            job = source.normalize(raw)
            assert job.title and job.company
            assert job.source == "himalayas"
            assert job.remote_status.value == "remote"
            assert job.description and len(job.description) > 200
        assert source.health().status.value == "healthy"
