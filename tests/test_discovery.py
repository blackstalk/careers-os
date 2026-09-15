from datetime import datetime, timezone

import pytest

from careers_os.career.search_profiles import SearchProfile
from careers_os.domain.enums import EmploymentType, RemoteStatus, SourceHealthStatus
from careers_os.domain.job import NormalizedJob
from careers_os.domain.query import JobSearchQuery
from careers_os.domain.raw_job import RawJob
from careers_os.ingestion.discovery import run_discovery
from careers_os.sources.base import JobSource, SourceHealth
from careers_os.storage.repository import JobRepository


class FakeJobSource(JobSource):
    """Minimal JobSource test double: returns whatever RawJobs are
    registered for a query keyword (case-insensitive substring match
    against title+description), mirroring how a real adapter's keyword
    search behaves without touching any network.
    """

    def __init__(self, name: str, catalog: list[RawJob]) -> None:
        self.name = name
        self.catalog = catalog
        self.search_calls: list[str] = []

    def search(self, query: JobSearchQuery) -> list[RawJob]:
        self.search_calls.append(query.keyword or "")
        if not query.keyword:
            return list(self.catalog)
        kw = query.keyword.lower()
        return [
            r for r in self.catalog
            if kw in (r.raw_title or "").lower() or kw in (r.raw_description or "").lower()
        ]

    def fetch_job(self, source_job_id: str) -> RawJob:
        return next(r for r in self.catalog if r.source_job_id == source_job_id)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        payload = raw.raw_payload
        return NormalizedJob(
            source=raw.source,
            source_job_id=raw.source_job_id,
            source_url=raw.source_url,
            title=raw.raw_title,
            company=payload.get("company"),
            location=raw.raw_location,
            remote_status=payload.get("remote_status", RemoteStatus.UNKNOWN),
            employment_type=payload.get("employment_type", EmploymentType.UNKNOWN),
            salary_min=payload.get("salary_min"),
            salary_max=payload.get("salary_max"),
            hourly_min=payload.get("hourly_min"),
            hourly_max=payload.get("hourly_max"),
            currency="USD" if payload.get("salary_min") or payload.get("hourly_min") else None,
            description=raw.raw_description,
            retrieved_at=raw.retrieved_at,
        )

    def health(self) -> SourceHealth:
        return SourceHealth(source=self.name, status=SourceHealthStatus.HEALTHY)

    def close(self) -> None:
        pass


def _raw(
    source: str, job_id: str, title: str, description: str, *,
    company: str = "Acme", employment_type: EmploymentType = EmploymentType.CONTRACT,
    salary_min=None, salary_max=None, hourly_min=None, hourly_max=None,
    remote_status: RemoteStatus = RemoteStatus.REMOTE,
) -> RawJob:
    return RawJob(
        source=source,
        source_job_id=job_id,
        source_url=f"https://example.com/{source}/{job_id}",
        raw_title=title,
        raw_description=description,
        raw_payload={
            "company": company,
            "employment_type": employment_type,
            "remote_status": remote_status,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "hourly_min": hourly_min,
            "hourly_max": hourly_max,
        },
        retrieved_at=datetime.now(timezone.utc),
    )


PHP_LARAVEL_BRIDGE_JOB = _raw(
    "srcA", "1", "Lead Laravel Platform Engineer",
    "Own the architecture for our Laravel and PHP platform, including AWS infrastructure, "
    "REST API integrations, and lead a small customer-facing solutions team.",
    hourly_min=90, hourly_max=120,
)
HIGH_PAY_FULL_TIME_PHP_JOB = _raw(
    "srcA", "2", "Senior PHP Developer",
    "Maintain our PHP monolith and fix bugs day to day.",
    employment_type=EmploymentType.FULL_TIME, salary_min=210000, salary_max=230000,
)
WORDPRESS_COMMODITY_JOB = _raw(
    "srcA", "3", "WordPress Developer",
    "Handle content updates, basic theme modifications, and landing page production using "
    "WordPress and PHP.",
    hourly_min=40, hourly_max=45,
)


@pytest.fixture
def profiles() -> list[SearchProfile]:
    return [
        SearchProfile(name="php", queries=["PHP"]),
        SearchProfile(name="laravel", queries=["Laravel"]),
    ]


class TestRunDiscovery:
    def test_job_matched_by_two_profiles_is_not_duplicated(self, db_session, profiles):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [PHP_LARAVEL_BRIDGE_JOB])
        result = run_discovery(
            repo, [("fake_family", source)], profiles, use_ai=False, employment_types=None,
        )
        assert result.metrics.unique_jobs == 1
        assert result.metrics.raw_jobs_discovered == 2  # found once per matching profile query
        opp = result.opportunities[0]
        assert set(opp.matched_profiles) == {"php", "laravel"}

    def test_metrics_reflect_actual_queries(self, db_session, profiles):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [PHP_LARAVEL_BRIDGE_JOB, WORDPRESS_COMMODITY_JOB])
        result = run_discovery(repo, [("fake_family", source)], profiles, use_ai=False)
        assert result.metrics.sources_queried == 1
        assert result.metrics.search_profiles == 2
        assert result.metrics.new_jobs == 2

    def test_default_employment_type_filter_excludes_full_time(self, db_session, profiles):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [HIGH_PAY_FULL_TIME_PHP_JOB, PHP_LARAVEL_BRIDGE_JOB])
        result = run_discovery(
            repo, [("fake_family", source)], profiles, use_ai=False,
            employment_types=[EmploymentType.CONTRACT, EmploymentType.FREELANCE],
        )
        titles = {o.job.title for o in result.opportunities}
        assert "Senior PHP Developer" not in titles
        assert "Lead Laravel Platform Engineer" in titles

    def test_employment_type_override_can_include_full_time(self, db_session, profiles):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [HIGH_PAY_FULL_TIME_PHP_JOB])
        result = run_discovery(
            repo, [("fake_family", source)], profiles, use_ai=False,
            employment_types=[EmploymentType.FULL_TIME],
        )
        assert result.metrics.unique_jobs == 1
        assert len(result.opportunities) == 1

    def test_conventional_high_paying_job_still_appears_when_included(self, db_session, profiles):
        # High comp + weak bridge role must still surface, never hard-excluded.
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [HIGH_PAY_FULL_TIME_PHP_JOB])
        result = run_discovery(
            repo, [("fake_family", source)], profiles, use_ai=False,
            employment_types=[EmploymentType.FULL_TIME],
        )
        assert len(result.opportunities) == 1
        from careers_os.career.bridge_role import BridgeClassification
        assert result.opportunities[0].bridge.classification in (
            BridgeClassification.WEAK, BridgeClassification.NONE, BridgeClassification.MODERATE,
        )

    def test_missing_compensation_does_not_exclude_a_job(self, db_session, profiles):
        repo = JobRepository(db_session)
        no_comp_job = _raw("srcA", "4", "Freelance PHP Consultant", "Do PHP consulting work with API integrations.")
        source = FakeJobSource("fake", [no_comp_job])
        result = run_discovery(repo, [("fake_family", source)], profiles, use_ai=False)
        assert result.metrics.unique_jobs == 1

    def test_min_fit_filters_out_low_scoring_jobs(self, db_session, profiles):
        repo = JobRepository(db_session)
        unrelated = _raw("srcA", "5", "PHP Data Entry Clerk", "Enter data using a PHP-based internal tool.")
        source = FakeJobSource("fake", [unrelated])
        result_no_filter = run_discovery(repo, [("fake_family", source)], profiles, use_ai=False)
        assert result_no_filter.metrics.unique_jobs == 1

        result_filtered = run_discovery(
            repo, [("fake_family", source)], profiles, use_ai=False, min_fit=0.99
        )
        assert result_filtered.opportunities == []

    def test_result_limit_is_respected(self, db_session, profiles):
        repo = JobRepository(db_session)
        many_jobs = [
            _raw("srcA", str(i), f"PHP Engineer {i}", "PHP development with APIs and integrations.")
            for i in range(10)
        ]
        source = FakeJobSource("fake", many_jobs)
        result = run_discovery(repo, [("fake_family", source)], profiles, use_ai=False, limit=3)
        assert len(result.opportunities) == 3
        assert result.metrics.unique_jobs == 10  # metrics count the full set, not just the displayed slice

    def test_cross_source_jobs_are_tracked_as_separate_opportunities(self, db_session, profiles):
        repo = JobRepository(db_session)
        job_a = _raw("srcA", "1", "PHP Engineer", "PHP development with APIs and integrations.")
        job_b = _raw("srcB", "1", "PHP Engineer", "PHP development with APIs and integrations.")
        source_a = FakeJobSource("fakeA", [job_a])
        source_b = FakeJobSource("fakeB", [job_b])
        result = run_discovery(
            repo, [("family_a", source_a), ("family_b", source_b)], profiles, use_ai=False,
        )
        assert result.metrics.sources_queried == 2
        assert result.metrics.unique_jobs == 2

    def test_zero_results_for_a_query_is_reported_not_hidden(self, db_session, profiles):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [])
        result = run_discovery(repo, [("fake_family", source)], profiles, use_ai=False)
        assert result.metrics.unique_jobs == 0
        assert any("found 0 jobs" in n for n in result.notes)

    def test_source_error_is_recorded_and_does_not_abort_the_run(self, db_session, profiles):
        repo = JobRepository(db_session)

        class BrokenSource(FakeJobSource):
            def search(self, query):
                raise RuntimeError("boom")

        broken = BrokenSource("broken", [])
        working = FakeJobSource("working", [PHP_LARAVEL_BRIDGE_JOB])
        result = run_discovery(
            repo, [("broken_family", broken), ("working_family", working)], profiles, use_ai=False,
        )
        assert result.metrics.unique_jobs == 1
        assert any("failed" in n for n in result.notes)
