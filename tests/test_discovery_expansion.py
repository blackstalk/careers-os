"""Phase 4.2 (sources) — application tracking, aggregator authority,
cross-source duplicate handling, and per-source funnel reporting, run
through the real `run_scheduled_pipeline`."""

from datetime import date, datetime, timedelta, timezone

import pytest

from careers_os.career.applications import ApplicationEntry, ApplicationLog, normalize_url
from careers_os.career.evidence_index import EvidenceIndex
from careers_os.career.preferences import AlertModeSettings, AlertPolicy, Preferences
from careers_os.career.search_profiles import SearchProfile
from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.enums import EmploymentType, JobStatus, OperatingMode, RemoteStatus, SourceHealthStatus
from careers_os.domain.evidence import Evidence, EvidenceProvenance
from careers_os.domain.job import NormalizedJob
from careers_os.domain.opportunity_decision import PursueRecommendation
from careers_os.domain.query import JobSearchQuery
from careers_os.domain.raw_job import RawJob
from careers_os.domain.resume import CareerRole
from careers_os.ingestion import pipeline
from careers_os.ingestion.discovery import run_discovery
from careers_os.ingestion.scheduled_run import run_scheduled_pipeline
from careers_os.notifications.channel import ChannelResult, NotificationChannel
from careers_os.sources.base import JobSource, SourceHealth
from careers_os.storage.repository import JobRepository

NOW = datetime.now(timezone.utc)
DESCRIPTION = (
    "Own solution architecture and AI-assisted automation systems using AWS, REST API "
    "integrations, and provide technical leadership and customer-facing delivery for "
    "enterprise clients."
)


class FakeSource(JobSource):
    def __init__(self, source: str, catalog: list[dict]) -> None:
        self.name = source
        self.source = source
        self.catalog = catalog

    def search(self, query: JobSearchQuery) -> list[RawJob]:
        return [
            RawJob(
                source=self.source, source_job_id=item["id"], source_url=item.get("url", f"https://{self.source}.test/{item['id']}"),
                raw_title=item["title"], raw_location=item.get("location"), raw_description=item.get("description", DESCRIPTION),
                raw_payload=item, retrieved_at=NOW,
            )
            for item in self.catalog
            if not query.keyword or query.keyword.lower() in item["title"].lower()
        ]

    def fetch_job(self, source_job_id: str) -> RawJob:
        raise NotImplementedError

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.raw_payload
        return NormalizedJob(
            source=raw.source, source_job_id=raw.source_job_id, source_url=raw.source_url,
            title=raw.raw_title, company=p.get("company", "Acme"), location=raw.raw_location,
            remote_status=p.get("remote_status", RemoteStatus.REMOTE),
            employment_type=EmploymentType.CONTRACT, hourly_min=150, hourly_max=180,
            description=raw.raw_description, retrieved_at=raw.retrieved_at,
        )

    def health(self) -> SourceHealth:
        return SourceHealth(source=self.name, status=SourceHealthStatus.HEALTHY)


class FakeChannel(NotificationChannel):
    name = "fake"

    def __init__(self) -> None:
        self.sent = []

    def send(self, content) -> ChannelResult:
        self.sent.append(content)
        return ChannelResult(success=True)


@pytest.fixture
def evidence_index() -> EvidenceIndex:
    role = CareerRole(id="acme-2019", company="Acme", title="Engineer", start_date=date(2019, 1, 1))
    prov = EvidenceProvenance(source_type="resume", source_name="fde", company="Acme", career_role_id=role.id,
                              variant_slug="fde", extracted_at=NOW)

    def ev(id_, type_, skills):
        return Evidence(id=id_, type=type_, statement=id_, canonical_skills=skills, provenance=prov,
                        start_date=role.start_date, end_date=role.end_date)

    return EvidenceIndex(
        evidence=[
            ev("ev-arch", "architecture", ["solutions_architecture", "aws", "rest_api", "integration"]),
            ev("ev-ai", "ai_ml", ["ai_ml", "automation"]),
            ev("ev-lead", "leadership", ["leadership", "customer_facing"]),
        ],
        career_roles=[role], taxonomy=SkillsTaxonomy.load(),
    )


PROFILES = [SearchProfile(name="arch", queries=["Architect"])]


def _prefs() -> Preferences:
    return Preferences.load().model_copy(update={
        "operating_mode": OperatingMode.ACTIVE,
        "alert_policy": AlertPolicy(
            passive=AlertModeSettings(minimum_pursue=PursueRecommendation.STRONG_PURSUE),
            active=AlertModeSettings(minimum_pursue=PursueRecommendation.PURSUE, max_alerts_per_run=10),
        ),
    })


def _run(db_session, evidence_index, sources, applications=None, channel=None):
    return run_scheduled_pipeline(
        JobRepository(db_session), sources, PROFILES, dry_run=False, channel=channel or FakeChannel(),
        use_ai=False, preferences=_prefs(), evidence_index=evidence_index,
        applications=applications if applications is not None else ApplicationLog(),
    )


def _statuses(result):
    return {(a.opportunity.job.source, a.opportunity.job.source_job_id): a.status for a in result.alerts}


class TestApplicationLog:
    def test_url_normalization_ignores_scheme_www_trailing_slash_and_apply_suffix(self):
        assert normalize_url("https://www.jobs.example.com/acme/123/application/") == normalize_url("http://jobs.example.com/acme/123")

    def test_matches_by_url_or_by_company_and_title(self):
        log = ApplicationLog(applications=[ApplicationEntry(company="WorkOS, Inc.", title="Applied AI Engineer", url="https://jobs.ashbyhq.com/workos/abc")])
        assert log.match(url="https://jobs.ashbyhq.com/workos/abc/application", company=None, title=None)
        assert log.match(url="https://himalayas.app/x", company="workos", title="Applied AI  Engineer")
        assert log.match(url="https://himalayas.app/x", company="workos", title="Platform Engineer") is None

    def test_add_updates_status_instead_of_duplicating(self):
        log = ApplicationLog()
        assert log.add(ApplicationEntry(company="Acme", title="Role", url="https://a.test/1"))
        assert not log.add(ApplicationEntry(company="Acme", title="Role", url="https://a.test/1"))
        assert log.add(ApplicationEntry(company="Acme", title="Role", url="https://a.test/1", status="rejected"))
        assert len(log.applications) == 1 and log.applications[0].status == "rejected"

    def test_save_and_load_round_trip(self, tmp_path):
        path = tmp_path / "applications.yaml"
        log = ApplicationLog(applications=[ApplicationEntry(company="Acme", title="Role", date=date(2026, 9, 16))])
        log.save(path)
        assert ApplicationLog.load(path).applications[0].date == date(2026, 9, 16)
        assert ApplicationLog.load(tmp_path / "missing.yaml").applications == []

    def test_committed_file_already_lists_the_workos_application(self):
        assert ApplicationLog.load().match(
            url="https://jobs.ashbyhq.com/workos/5e650527-d8dd-413a-9cfb-d7d68143274b", company=None, title=None,
        )


class TestApplicationSuppression:
    def test_applied_job_is_never_emailed(self, db_session, evidence_index):
        log = ApplicationLog(applications=[ApplicationEntry(company="Acme", title="Lead Solutions Architect")])
        channel = FakeChannel()
        result = _run(db_session, evidence_index, [("ashby", FakeSource("ashby", [{"id": "1", "title": "Lead Solutions Architect"}]))],
                      applications=log, channel=channel)
        assert channel.sent == []
        assert result.metrics.applications_suppressed == 1
        assert _statuses(result) == {("ashby", "1"): "already_applied"}
        job = JobRepository(db_session).get_job("ashby", "1")
        assert JobRepository(db_session).list_notifications(job.id) == []

    def test_local_applied_status_also_suppresses(self, db_session, evidence_index):
        source = FakeSource("ashby", [{"id": "1", "title": "Lead Solutions Architect"}])
        _run(db_session, evidence_index, [("ashby", source)], channel=FakeChannel())  # first run emails it
        repo = JobRepository(db_session)
        repo.get_job("ashby", "1").status = JobStatus.APPLIED.value
        db_session.commit()
        # A new, unsent job with the same company and title would otherwise alert;
        # here the same job is re-found and must be skipped for being applied.
        result = _run(db_session, evidence_index, [("ashby", source)], channel=FakeChannel())
        assert result.metrics.applications_suppressed == 1

    def test_aggregator_copy_of_an_applied_job_is_suppressed(self, db_session, evidence_index):
        log = ApplicationLog(applications=[ApplicationEntry(company="Acme", title="Lead Solutions Architect", url="https://ashby.test/1")])
        result = _run(db_session, evidence_index, [("himalayas", FakeSource("himalayas", [{"id": "h1", "title": "Lead Solutions Architect"}]))],
                      applications=log)
        assert _statuses(result) == {("himalayas", "h1"): "already_applied"}


class TestAggregatorAuthority:
    def test_aggregator_copy_defers_to_the_ats_posting_found_in_the_same_run(self, db_session, evidence_index):
        channel = FakeChannel()
        result = _run(db_session, evidence_index, [
            ("ashby", FakeSource("ashby", [{"id": "1", "title": "Lead Solutions Architect"}])),
            ("himalayas", FakeSource("himalayas", [{"id": "h1", "title": "Lead Solutions Architect"}])),
        ], channel=channel)
        statuses = _statuses(result)
        assert statuses[("ashby", "1")] == "sent"
        assert statuses[("himalayas", "h1")] == "superseded"
        assert len(channel.sent) == 1
        assert result.metrics.superseded_by_authoritative == 1

    def test_aggregator_only_job_still_alerts(self, db_session, evidence_index):
        channel = FakeChannel()
        result = _run(db_session, evidence_index, [("himalayas", FakeSource("himalayas", [{"id": "h1", "title": "Lead Solutions Architect"}]))], channel=channel)
        assert _statuses(result) == {("himalayas", "h1"): "sent"}

    def test_remote_aggregator_copy_cannot_override_a_hybrid_ats_posting(self, db_session, evidence_index):
        channel = FakeChannel()
        result = _run(db_session, evidence_index, [
            ("ashby", FakeSource("ashby", [{"id": "1", "title": "Lead Solutions Architect", "remote_status": RemoteStatus.HYBRID}])),
            ("himalayas", FakeSource("himalayas", [{"id": "h1", "title": "Lead Solutions Architect"}])),
        ], channel=channel)
        assert channel.sent == []
        assert _statuses(result) == {("himalayas", "h1"): "superseded"}

    def test_duplicate_already_emailed_from_another_source_is_not_emailed_again(self, db_session, evidence_index):
        first = FakeChannel()
        _run(db_session, evidence_index, [("ashby", FakeSource("ashby", [{"id": "1", "title": "Lead Solutions Architect"}]))], channel=first)
        assert len(first.sent) == 1
        second = FakeChannel()
        result = _run(db_session, evidence_index, [("himalayas", FakeSource("himalayas", [{"id": "h1", "title": "Lead Solutions Architect"}]))], channel=second)
        assert second.sent == []
        assert _statuses(result) == {("himalayas", "h1"): "suppressed_duplicate"}

    def test_different_jobs_sharing_boilerplate_description_are_not_treated_as_duplicates(self, db_session, evidence_index):
        channel = FakeChannel()
        _run(db_session, evidence_index, [
            ("ashby", FakeSource("ashby", [{"id": "1", "title": "Lead Solutions Architect", "company": "Acme"}])),
            ("himalayas", FakeSource("himalayas", [{"id": "h1", "title": "Principal Architect", "company": "Globex"}])),
        ], channel=channel)
        assert len(channel.sent) == 2


class TestSourceFunnel:
    def test_reports_per_source_counts_and_unique_contributions(self, db_session, evidence_index):
        result = _run(db_session, evidence_index, [
            ("ashby", FakeSource("ashby", [{"id": "1", "title": "Lead Solutions Architect"}])),
            ("himalayas", FakeSource("himalayas", [
                {"id": "h1", "title": "Lead Solutions Architect"},
                {"id": "h2", "title": "Staff Architect", "company": "Globex", "description": "Different text entirely about architecture work."},
            ])),
        ])
        ashby, him = result.metrics.sources["ashby"], result.metrics.sources["himalayas"]
        assert (ashby.raw, ashby.unique_jobs, ashby.unique_to_source) == (1, 1, 0)
        assert (him.raw, him.unique_jobs, him.unique_to_source) == (2, 2, 1)
        assert ashby.eligible == 1 and ashby.alert_threshold == 1

    def test_failed_source_is_reported_not_silent(self, db_session, evidence_index):
        class Broken(FakeSource):
            def search(self, query):
                err = RuntimeError("API shape changed")
                err.kind = "schema_changed"
                raise err

        result = _run(db_session, evidence_index, [("himalayas", Broken("himalayas", []))])
        funnel = result.metrics.sources["himalayas"]
        assert funnel.raw == 0
        assert funnel.failures and funnel.failures[0].startswith("schema_changed")


class TestDedupPrefilterAndScoreCache:
    def test_copy_with_different_title_and_company_but_identical_text_is_still_flagged(self, db_session):
        repo = JobRepository(db_session)
        text = "A long, specific job description that was copied word for word by a reposting site. " * 5
        for source, title, company in [("ashby", "Platform Engineer", "Acme"), ("himalayas", "Sr Platform Eng", "Acme Staffing")]:
            job = NormalizedJob(source=source, source_job_id="x", source_url="u", title=title, company=company,
                                description=text, retrieved_at=NOW)
            repo.upsert_job(job, RawJob(source=source, source_job_id="x", source_url="u", raw_payload={}, retrieved_at=NOW))
        assert len(repo.list_duplicates()) == 1

    def test_same_job_found_by_two_queries_is_scored_once(self, db_session, evidence_index, monkeypatch):
        calls = []
        real = pipeline.score_job
        monkeypatch.setattr(pipeline, "score_job", lambda *a, **k: calls.append(1) or real(*a, **k))
        profiles = [SearchProfile(name="a", queries=["Architect", "Solutions"])]
        run_discovery(JobRepository(db_session), [("ashby", FakeSource("ashby", [{"id": "1", "title": "Lead Solutions Architect"}]))],
                      profiles, use_ai=False, evidence_index=evidence_index)
        assert len(calls) == 1


class TestPhase44Ranking:
    """Stale postings ranked alongside fresh ones for the same alert budget."""

    def test_stale_postings_rank_below_equivalent_fresh_ones(self):
        from careers_os.career.discovery_ranking import apply_freshness_penalty
        from careers_os.domain.opportunity_decision import Freshness

        assert apply_freshness_penalty(0.9, Freshness.STALE) < 0.9
        for level in (Freshness.FRESH, Freshness.RECENT, Freshness.AGING, Freshness.UNKNOWN):
            assert apply_freshness_penalty(0.9, level) == 0.9

    def test_a_stale_job_is_outranked_in_a_real_run(self, db_session, evidence_index):
        old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        catalog = [
            {"id": "fresh", "title": "Lead Solutions Architect", "company": "Fresh Co"},
            {"id": "stale", "title": "Lead Solutions Architect", "company": "Stale Co", "posted_at": old},
        ]
        result = _run(db_session, evidence_index, [("ashby", StaleAwareSource("ashby", catalog))])
        ordered = [a.opportunity.job.company for a in result.alerts]
        assert ordered.index("Fresh Co") < ordered.index("Stale Co")


class StaleAwareSource(FakeSource):
    def normalize(self, raw):
        job = super().normalize(raw)
        posted = raw.raw_payload.get("posted_at")
        return job.model_copy(update={"posted_at": datetime.fromisoformat(posted)}) if posted else job
