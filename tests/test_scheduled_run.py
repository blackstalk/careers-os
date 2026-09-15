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


def _raw(job_id: str, title: str, description: str, **overrides) -> RawJob:
    payload = {"company": "Acme", "employment_type": EmploymentType.CONTRACT, "remote_status": RemoteStatus.REMOTE}
    payload.update(overrides)
    return RawJob(
        source="srcA", source_job_id=job_id, source_url=f"https://example.com/{job_id}",
        raw_title=title, raw_description=description, raw_payload=payload, retrieved_at=NOW,
    )


# A role with strong architecture/AI/leadership signal alongside the
# discovery-anchor tech, high pay, and no hard gaps — should reliably
# reach strong_pursue given the default career profile.
_STRONG_JOB = _raw(
    "1", "Lead Solutions Architect",
    "Own solution architecture and AI-assisted automation systems using AWS, REST API "
    "integrations, and provide technical leadership and customer-facing delivery for "
    "enterprise clients.",
    hourly_min=150, hourly_max=180,
)
_WEAK_JOB = _raw("2", "Data Entry Clerk", "Enter data into spreadsheets using a PHP tool.")


@pytest.fixture
def profiles() -> list[SearchProfile]:
    return [SearchProfile(name="php", queries=["PHP"]), SearchProfile(name="arch", queries=["Architect"])]


@pytest.fixture
def evidence_index() -> EvidenceIndex:
    # Enough real, dated evidence to let _STRONG_JOB's requirements resolve
    # to strong_match (qualification=strong) rather than "unknown" (no
    # resume imported) — mirrors tests/test_regression_real_cases.py.
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


@pytest.fixture
def passive_preferences() -> Preferences:
    base = Preferences.load()
    return base.model_copy(update={
        "operating_mode": OperatingMode.PASSIVE,
        "alert_policy": AlertPolicy(
            passive=AlertModeSettings(minimum_pursue=PursueRecommendation.STRONG_PURSUE),
            active=AlertModeSettings(minimum_pursue=PursueRecommendation.PURSUE),
        ),
    })


class TestDryRun:
    def test_dry_run_never_sends_and_never_persists_notification(self, db_session, profiles, passive_preferences, evidence_index):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [_STRONG_JOB])
        channel = FakeChannel()
        result = run_scheduled_pipeline(
            repo, [("fake_family", source)], profiles, dry_run=True, channel=channel,
            use_ai=False, preferences=passive_preferences, evidence_index=evidence_index,
        )
        assert channel.sent == []
        alertable = [a for a in result.alerts if a.status == "dry_run"]
        assert len(alertable) >= 1
        job_id = alertable[0].opportunity.job.id
        assert repo.list_notifications(job_id) == []

    def test_dry_run_reports_what_would_have_been_sent(self, db_session, profiles, passive_preferences, evidence_index):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [_STRONG_JOB, _WEAK_JOB])
        result = run_scheduled_pipeline(
            repo, [("fake_family", source)], profiles, dry_run=True, channel=None,
            use_ai=False, preferences=passive_preferences, evidence_index=evidence_index,
        )
        assert result.metrics.jobs_evaluated == 2
        dry_run_alerts = [a for a in result.alerts if a.status == "dry_run"]
        assert len(dry_run_alerts) == 1
        assert dry_run_alerts[0].opportunity.job.title == "Lead Solutions Architect"


class TestRealSend:
    def test_strong_opportunity_triggers_a_sent_notification(self, db_session, profiles, passive_preferences, evidence_index):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [_STRONG_JOB])
        channel = FakeChannel(succeed=True)
        result = run_scheduled_pipeline(
            repo, [("fake_family", source)], profiles, dry_run=False, channel=channel,
            use_ai=False, preferences=passive_preferences, evidence_index=evidence_index,
        )
        assert result.metrics.notifications_sent == 1
        assert len(channel.sent) == 1

    def test_weak_opportunity_never_triggers_a_notification(self, db_session, profiles, passive_preferences, evidence_index):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [_WEAK_JOB])
        channel = FakeChannel(succeed=True)
        result = run_scheduled_pipeline(
            repo, [("fake_family", source)], profiles, dry_run=False, channel=channel,
            use_ai=False, preferences=passive_preferences, evidence_index=evidence_index,
        )
        assert result.metrics.notifications_sent == 0
        assert channel.sent == []

    def test_a_successful_send_is_persisted_as_sent(self, db_session, profiles, passive_preferences, evidence_index):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [_STRONG_JOB])
        channel = FakeChannel(succeed=True)
        run_scheduled_pipeline(
            repo, [("fake_family", source)], profiles, dry_run=False, channel=channel,
            use_ai=False, preferences=passive_preferences, evidence_index=evidence_index,
        )
        job = repo.get_job("srcA", "1")
        sent = repo.list_notifications(job.id, status="sent")
        assert len(sent) == 1


class TestFailedSendNotPersistedAsAlerted:
    def test_failed_send_does_not_mark_as_alerted(self, db_session, profiles, passive_preferences, evidence_index):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [_STRONG_JOB])
        channel = FakeChannel(succeed=False)
        result = run_scheduled_pipeline(
            repo, [("fake_family", source)], profiles, dry_run=False, channel=channel,
            use_ai=False, preferences=passive_preferences, evidence_index=evidence_index,
        )
        assert result.metrics.failures == 1
        assert result.metrics.notifications_sent == 0
        job = repo.get_job("srcA", "1")
        assert repo.list_notifications(job.id, status="sent") == []
        assert len(repo.list_notifications(job.id, status="failed")) == 1

    def test_failed_send_does_not_suppress_a_subsequent_retry(self, db_session, profiles, passive_preferences, evidence_index):
        repo = JobRepository(db_session)
        source_fail = FakeJobSource("fake", [_STRONG_JOB])
        failing_channel = FakeChannel(succeed=False)
        run_scheduled_pipeline(
            repo, [("fake_family", source_fail)], profiles, dry_run=False, channel=failing_channel,
            use_ai=False, preferences=passive_preferences, evidence_index=evidence_index,
        )
        source_retry = FakeJobSource("fake", [_STRONG_JOB])
        working_channel = FakeChannel(succeed=True)
        result = run_scheduled_pipeline(
            repo, [("fake_family", source_retry)], profiles, dry_run=False, channel=working_channel,
            use_ai=False, preferences=passive_preferences, evidence_index=evidence_index,
        )
        assert result.metrics.notifications_sent == 1


class TestDuplicateSuppression:
    def test_second_run_suppresses_an_already_alerted_opportunity(self, db_session, profiles, passive_preferences, evidence_index):
        repo = JobRepository(db_session)

        source1 = FakeJobSource("fake", [_STRONG_JOB])
        channel = FakeChannel(succeed=True)
        run_scheduled_pipeline(
            repo, [("fake_family", source1)], profiles, dry_run=False, channel=channel,
            use_ai=False, preferences=passive_preferences, evidence_index=evidence_index,
        )
        assert len(channel.sent) == 1

        source2 = FakeJobSource("fake", [_STRONG_JOB])
        result2 = run_scheduled_pipeline(
            repo, [("fake_family", source2)], profiles, dry_run=False, channel=channel,
            use_ai=False, preferences=passive_preferences, evidence_index=evidence_index,
        )
        assert len(channel.sent) == 1  # unchanged — no second send
        assert result2.metrics.notifications_suppressed_duplicate == 1
        assert result2.metrics.notifications_sent == 0

    def test_missing_channel_never_marks_as_sent(self, db_session, profiles, passive_preferences, evidence_index):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [_STRONG_JOB])
        result = run_scheduled_pipeline(
            repo, [("fake_family", source)], profiles, dry_run=False, channel=None,
            use_ai=False, preferences=passive_preferences, evidence_index=evidence_index,
        )
        assert result.metrics.failures == 1
        job = repo.get_job("srcA", "1")
        assert repo.list_notifications(job.id, status="sent") == []


class TestNoAlertPolicyConfigured:
    def test_missing_alert_policy_runs_discovery_but_sends_nothing(self, db_session, profiles):
        repo = JobRepository(db_session)
        preferences = Preferences.load().model_copy(update={"alert_policy": None})
        source = FakeJobSource("fake", [_STRONG_JOB])
        channel = FakeChannel(succeed=True)
        result = run_scheduled_pipeline(
            repo, [("fake_family", source)], profiles, dry_run=False, channel=channel,
            use_ai=False, preferences=preferences,
        )
        assert result.metrics.jobs_evaluated == 1
        assert channel.sent == []
        assert any("no alert_policy" in n.lower() for n in result.notes)


class TestMetrics:
    def test_metrics_reflect_eligible_and_pursue_threshold_counts(self, db_session, profiles, passive_preferences, evidence_index):
        repo = JobRepository(db_session)
        source = FakeJobSource("fake", [_STRONG_JOB, _WEAK_JOB])
        result = run_scheduled_pipeline(
            repo, [("fake_family", source)], profiles, dry_run=True, channel=None,
            use_ai=False, preferences=passive_preferences, evidence_index=evidence_index,
        )
        assert result.metrics.jobs_evaluated == 2
        assert result.metrics.eligible_count == 2  # both are geographically/authorization-wise eligible
        assert result.metrics.alert_threshold_count == 1  # only the strong job crosses passive's threshold
