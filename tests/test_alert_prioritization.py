"""Phase 4.1 — alert prioritization, opportunity clustering, and the
per-run alert budget, exercised through the real `run_scheduled_pipeline`
(not just the isolated unit tests in test_clustering.py). See
docs/alerts.md#alert-prioritization-and-opportunity-clustering.
"""

from datetime import date, datetime, timezone

import pytest

from careers_os.career.evidence_index import EvidenceIndex
from careers_os.career.preferences import AlertModeSettings, AlertPolicy, Preferences
from careers_os.career.search_profiles import SearchProfile
from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.enums import EmploymentType, OperatingMode, RemoteStatus, SourceHealthStatus
from careers_os.domain.evidence import Evidence, EvidenceProvenance
from careers_os.domain.job import NormalizedJob
from careers_os.domain.opportunity_decision import PursueRecommendation
from careers_os.domain.query import JobSearchQuery
from careers_os.domain.raw_job import RawJob
from careers_os.domain.resume import CareerRole
from careers_os.ingestion.scheduled_run import run_scheduled_pipeline
from careers_os.notifications.channel import ChannelResult, NotificationChannel
from careers_os.sources.base import JobSource, SourceHealth
from careers_os.storage.repository import JobRepository

NOW = datetime.now(timezone.utc)

_STRONG_DESCRIPTION = (
    "Own solution architecture and AI-assisted automation systems using AWS, REST API "
    "integrations, and provide technical leadership and customer-facing delivery for "
    "enterprise clients."
)


class FakeJobSource(JobSource):
    def __init__(self, name: str, catalog: list[RawJob]) -> None:
        self.name = name
        self.catalog = catalog

    def search(self, query: JobSearchQuery) -> list[RawJob]:
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
            source=raw.source, source_job_id=raw.source_job_id, source_url=raw.source_url,
            title=raw.raw_title, company=payload.get("company"), location=raw.raw_location,
            remote_status=payload.get("remote_status", RemoteStatus.UNKNOWN),
            employment_type=payload.get("employment_type", EmploymentType.UNKNOWN),
            salary_min=payload.get("salary_min"), salary_max=payload.get("salary_max"),
            hourly_min=payload.get("hourly_min"), hourly_max=payload.get("hourly_max"),
            description=raw.raw_description, retrieved_at=raw.retrieved_at,
        )

    def health(self) -> SourceHealth:
        return SourceHealth(source=self.name, status=SourceHealthStatus.HEALTHY)

    def close(self) -> None:
        pass


class FakeChannel(NotificationChannel):
    name = "fake"

    def __init__(self, succeed: bool = True) -> None:
        self.succeed = succeed
        self.sent: list = []

    def send(self, content) -> ChannelResult:
        self.sent.append(content)
        if self.succeed:
            return ChannelResult(success=True)
        return ChannelResult(success=False, error="fake failure")


def _raw(source: str, job_id: str, title: str, location: str | None = None, **overrides) -> RawJob:
    payload = {"company": "Acme", "employment_type": EmploymentType.CONTRACT, "remote_status": RemoteStatus.REMOTE}
    payload.update(overrides)
    return RawJob(
        source=source, source_job_id=job_id, source_url=f"https://example.com/{source}/{job_id}",
        raw_title=title, raw_location=location, raw_description=_STRONG_DESCRIPTION,
        raw_payload=payload, retrieved_at=NOW,
    )


def _strong(source: str, job_id: str, title: str, company: str = "Acme", location: str | None = None, hourly_min=150, hourly_max=180) -> RawJob:
    return _raw(source, job_id, title, location=location, company=company, hourly_min=hourly_min, hourly_max=hourly_max)


@pytest.fixture
def profiles() -> list[SearchProfile]:
    return [SearchProfile(name="arch", queries=["Architect", "Engineer", "Lead"])]


@pytest.fixture
def evidence_index() -> EvidenceIndex:
    role = CareerRole(id="acme-2019", company="Acme", title="Engineer", start_date=date(2019, 1, 1))
    prov = EvidenceProvenance(
        source_type="resume", source_name="fde", company="Acme", career_role_id=role.id,
        variant_slug="fde", extracted_at=datetime.now(timezone.utc),
    )
    evidence = [
        Evidence(
            id="ev-arch", type="architecture", statement="Owned solution architecture and AWS infrastructure.",
            canonical_skills=["solutions_architecture", "aws", "rest_api", "integration"],
            provenance=prov, start_date=role.start_date, end_date=role.end_date,
        ),
        Evidence(
            id="ev-ai", type="ai_ml", statement="Built AI-assisted automation systems.",
            canonical_skills=["ai_ml", "automation"], provenance=prov,
            start_date=role.start_date, end_date=role.end_date,
        ),
        Evidence(
            id="ev-lead", type="leadership", statement="Provided technical leadership for the team.",
            canonical_skills=["leadership", "customer_facing"], provenance=prov,
            start_date=role.start_date, end_date=role.end_date,
        ),
    ]
    return EvidenceIndex(evidence=evidence, career_roles=[role], taxonomy=SkillsTaxonomy.load())


def _preferences(max_alerts_per_run: int = 5) -> Preferences:
    # Active mode (threshold=pursue) is used here rather than passive
    # (threshold=strong_pursue) so every synthetic fixture is uniformly
    # alert-eligible regardless of title — career-direction assessment
    # (career/opportunity_value.py) is title-sensitive (a title
    # containing "Architect" reaches "strong" direction; others land at
    # "moderate"), which pushes some fixtures to the `pursue` tier
    # instead of `strong_pursue`. Both tiers pass `alert_rank` >= pursue,
    # so this only affects which threshold is used, not the clustering/
    # budget/state-invariant behavior under test.
    base = Preferences.load()
    return base.model_copy(update={
        "operating_mode": OperatingMode.ACTIVE,
        "alert_policy": AlertPolicy(
            passive=AlertModeSettings(minimum_pursue=PursueRecommendation.STRONG_PURSUE),
            active=AlertModeSettings(
                minimum_pursue=PursueRecommendation.PURSUE, max_alerts_per_run=max_alerts_per_run,
            ),
        ),
    })


# Five distinct families across four companies, three of which are the
# same underlying "Lead Solutions Architect" role posted for different
# locations/segments (Acme) — mirrors the real OpenAI/Ashby situation
# that motivated Phase 4.1.
_ACME_VARIANT_1 = _strong("ashby", "acme-1", "Lead Solutions Architect", location="San Francisco")
_ACME_VARIANT_2 = _strong("greenhouse", "acme-2", "Lead Solutions Architect, Enterprise", location="New York, NY")
_ACME_VARIANT_3 = _strong("lever", "acme-3", "Lead Solutions Architect - Remote", location="Remote")
_ACME_DIFFERENT_ROLE = _strong("ashby", "acme-4", "Principal AI Platform Engineer", location="San Francisco")
_GLOBEX_SAME_TITLE = _strong("greenhouse", "globex-1", "Lead Solutions Architect", company="Globex", location="Austin, TX")
_INITECH_ROLE = _strong("lever", "initech-1", "Senior ML Systems Engineer", company="Initech", location="Boston, MA")
_UMBRELLA_ROLE = _strong("ashby", "umbrella-1", "Customer Engineering Lead", company="Umbrella", location="Chicago, IL")

_ALL_FIVE_CLUSTERS = [
    _ACME_VARIANT_1, _ACME_VARIANT_2, _ACME_VARIANT_3,
    _ACME_DIFFERENT_ROLE, _GLOBEX_SAME_TITLE, _INITECH_ROLE, _UMBRELLA_ROLE,
]


class TestClusteringIntegration:
    def test_near_duplicate_postings_collapse_to_one_cluster(self, db_session, profiles, evidence_index):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [_ACME_VARIANT_1, _ACME_VARIANT_2, _ACME_VARIANT_3])
        result = run_scheduled_pipeline(
            repo, [("fake", source)], profiles, dry_run=True, use_ai=False,
            preferences=_preferences(), evidence_index=evidence_index,
        )
        assert result.metrics.alert_threshold_count == 3
        assert result.metrics.opportunity_clusters == 1
        assert result.metrics.clustered_variant_count == 2
        assert len(result.alerts) == 1
        assert result.alerts[0].related_variant_count == 2

    def test_different_role_same_company_is_a_separate_cluster(self, db_session, profiles, evidence_index):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [_ACME_VARIANT_1, _ACME_DIFFERENT_ROLE])
        result = run_scheduled_pipeline(
            repo, [("fake", source)], profiles, dry_run=True, use_ai=False,
            preferences=_preferences(), evidence_index=evidence_index,
        )
        assert result.metrics.opportunity_clusters == 2

    def test_same_title_different_company_is_a_separate_cluster(self, db_session, profiles, evidence_index):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [_ACME_VARIANT_1, _GLOBEX_SAME_TITLE])
        result = run_scheduled_pipeline(
            repo, [("fake", source)], profiles, dry_run=True, use_ai=False,
            preferences=_preferences(), evidence_index=evidence_index,
        )
        assert result.metrics.opportunity_clusters == 2

    def test_provider_identity_does_not_affect_clustering_or_ranking(self, db_session, profiles, evidence_index):
        # _ACME_VARIANT_1/2/3 are deliberately sourced from ashby/greenhouse/lever
        # respectively — they must still cluster as one family.
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [_ACME_VARIANT_1, _ACME_VARIANT_2, _ACME_VARIANT_3])
        result = run_scheduled_pipeline(
            repo, [("fake", source)], profiles, dry_run=True, use_ai=False,
            preferences=_preferences(), evidence_index=evidence_index,
        )
        assert result.metrics.opportunity_clusters == 1


class TestAlertBudget:
    def test_fewer_candidates_than_budget_all_proceed(self, db_session, profiles, evidence_index):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [_ACME_VARIANT_1, _ACME_DIFFERENT_ROLE])
        result = run_scheduled_pipeline(
            repo, [("fake", source)], profiles, dry_run=True, use_ai=False,
            preferences=_preferences(max_alerts_per_run=5), evidence_index=evidence_index,
        )
        assert result.metrics.alerts_selected == 2
        assert result.metrics.alerts_deferred == 0
        assert all(a.status == "dry_run" for a in result.alerts)

    def test_exactly_budget_candidates_all_proceed(self, db_session, profiles, evidence_index):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [_ACME_VARIANT_1, _ACME_DIFFERENT_ROLE, _GLOBEX_SAME_TITLE])
        result = run_scheduled_pipeline(
            repo, [("fake", source)], profiles, dry_run=True, use_ai=False,
            preferences=_preferences(max_alerts_per_run=3), evidence_index=evidence_index,
        )
        assert result.metrics.alerts_selected == 3
        assert result.metrics.alerts_deferred == 0

    def test_more_candidates_than_budget_caps_selection(self, db_session, profiles, evidence_index):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", _ALL_FIVE_CLUSTERS)
        result = run_scheduled_pipeline(
            repo, [("fake", source)], profiles, dry_run=True, use_ai=False,
            preferences=_preferences(max_alerts_per_run=2), evidence_index=evidence_index,
        )
        assert result.metrics.opportunity_clusters == 5
        assert result.metrics.alert_budget == 2
        assert result.metrics.alerts_selected == 2
        assert result.metrics.alerts_deferred == 3
        assert sum(1 for a in result.alerts if a.status == "dry_run") == 2
        assert sum(1 for a in result.alerts if a.status == "deferred") == 3

    def test_configuration_override_changes_the_budget(self, db_session, profiles, evidence_index):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", _ALL_FIVE_CLUSTERS)
        result = run_scheduled_pipeline(
            repo, [("fake", source)], profiles, dry_run=True, use_ai=False,
            preferences=_preferences(max_alerts_per_run=4), evidence_index=evidence_index,
        )
        assert result.metrics.alert_budget == 4
        assert result.metrics.alerts_selected == 4
        assert result.metrics.alerts_deferred == 1

    def test_real_send_respects_the_budget(self, db_session, profiles, evidence_index):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", _ALL_FIVE_CLUSTERS)
        channel = FakeChannel(succeed=True)
        result = run_scheduled_pipeline(
            repo, [("fake", source)], profiles, dry_run=False, channel=channel, use_ai=False,
            preferences=_preferences(max_alerts_per_run=2), evidence_index=evidence_index,
        )
        assert result.metrics.notifications_sent == 2
        assert len(channel.sent) == 2


class TestDeferredStateInvariant:
    def test_deferred_opportunities_are_never_persisted_as_alerted(self, db_session, profiles, evidence_index):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", _ALL_FIVE_CLUSTERS)
        channel = FakeChannel(succeed=True)
        result = run_scheduled_pipeline(
            repo, [("fake", source)], profiles, dry_run=False, channel=channel, use_ai=False,
            preferences=_preferences(max_alerts_per_run=2), evidence_index=evidence_index,
        )
        deferred_outcomes = [a for a in result.alerts if a.status == "deferred"]
        assert len(deferred_outcomes) == 3
        for outcome in deferred_outcomes:
            job_id = outcome.opportunity.job.id
            assert repo.list_notifications(job_id) == []

    def test_dry_run_persists_no_notification_state_even_with_clustering_and_budget(
        self, db_session, profiles, evidence_index,
    ):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", _ALL_FIVE_CLUSTERS)
        result = run_scheduled_pipeline(
            repo, [("fake", source)], profiles, dry_run=True, use_ai=False,
            preferences=_preferences(max_alerts_per_run=2), evidence_index=evidence_index,
        )
        assert len(result.alerts) == 5
        for outcome in result.alerts:
            job_id = outcome.opportunity.job.id
            assert repo.list_notifications(job_id) == []

    def test_deferred_opportunity_can_still_send_on_a_later_run_with_more_budget(
        self, db_session, profiles, evidence_index,
    ):
        repo = JobRepository(db_session)
        channel = FakeChannel(succeed=True)

        first_source = FakeJobSource("fake", _ALL_FIVE_CLUSTERS)
        first = run_scheduled_pipeline(
            repo, [("fake", first_source)], profiles, dry_run=False, channel=channel, use_ai=False,
            preferences=_preferences(max_alerts_per_run=2), evidence_index=evidence_index,
        )
        assert first.metrics.notifications_sent == 2
        deferred_job_ids = {a.opportunity.job.id for a in first.alerts if a.status == "deferred"}
        assert len(deferred_job_ids) == 3

        second_source = FakeJobSource("fake", _ALL_FIVE_CLUSTERS)
        second = run_scheduled_pipeline(
            repo, [("fake", second_source)], profiles, dry_run=False, channel=channel, use_ai=False,
            preferences=_preferences(max_alerts_per_run=10), evidence_index=evidence_index,
        )
        # The 2 already-sent clusters must be suppressed as duplicates,
        # not re-sent — only the previously-deferred ones should send now.
        assert second.metrics.notifications_suppressed_duplicate == 2
        assert second.metrics.notifications_sent == 3
        newly_sent_job_ids = {
            outcome.opportunity.job.id for outcome in second.alerts if outcome.status == "sent"
        }
        assert newly_sent_job_ids == deferred_job_ids

    def test_deferred_opportunity_evaluation_is_still_persisted(self, db_session, profiles, evidence_index):
        # Clustering/budget only gate NOTIFICATION selection — evaluation
        # history persistence is unaffected, exactly like Phase 4.
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", _ALL_FIVE_CLUSTERS)
        result = run_scheduled_pipeline(
            repo, [("fake", source)], profiles, dry_run=True, use_ai=False,
            preferences=_preferences(max_alerts_per_run=2), evidence_index=evidence_index,
        )
        deferred = next(a for a in result.alerts if a.status == "deferred")
        job = deferred.opportunity.job
        assert repo.get_latest_evaluation(job.id) is not None


class TestExistingBehaviorPreserved:
    def test_previously_alerted_cluster_representative_is_suppressed_next_run(
        self, db_session, profiles, evidence_index,
    ):
        repo = JobRepository(db_session)
        channel = FakeChannel(succeed=True)
        source1 = FakeJobSource("fake", [_ACME_VARIANT_1])
        run_scheduled_pipeline(
            repo, [("fake", source1)], profiles, dry_run=False, channel=channel, use_ai=False,
            preferences=_preferences(), evidence_index=evidence_index,
        )
        source2 = FakeJobSource("fake", [_ACME_VARIANT_1, _ACME_VARIANT_2, _ACME_VARIANT_3])
        result2 = run_scheduled_pipeline(
            repo, [("fake", source2)], profiles, dry_run=False, channel=channel, use_ai=False,
            preferences=_preferences(), evidence_index=evidence_index,
        )
        assert result2.metrics.notifications_suppressed_duplicate == 1
        assert result2.metrics.notifications_sent == 0

    def test_single_opportunity_run_behaves_exactly_as_before_clustering_existed(
        self, db_session, profiles, evidence_index,
    ):
        repo = JobRepository(db_session)
        channel = FakeChannel(succeed=True)
        source = FakeJobSource("fake", [_ACME_VARIANT_1])
        result = run_scheduled_pipeline(
            repo, [("fake", source)], profiles, dry_run=False, channel=channel, use_ai=False,
            preferences=_preferences(), evidence_index=evidence_index,
        )
        assert result.metrics.opportunity_clusters == 1
        assert result.metrics.alerts_selected == 1
        assert result.metrics.alerts_deferred == 0
        assert result.metrics.notifications_sent == 1
