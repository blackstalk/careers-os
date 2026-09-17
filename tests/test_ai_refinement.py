"""Phase 4.3 — bounded AI refinement of finalists.

Claude is never called here: `scoring.ai.readiness`/`evaluate` are
replaced with fakes, and conftest.py removes ANTHROPIC_API_KEY for every
offline test.
"""

import pytest
import yaml

from careers_os.career.applications import ApplicationEntry, ApplicationLog
from careers_os.career.preferences import DEFAULT_PREFERENCES_PATH, AIRefinementSettings, Preferences
from careers_os.domain.opportunity_decision import PursueRecommendation
from careers_os.ingestion.ai_refinement import refine_finalists
from careers_os.ingestion.discovery import run_discovery
from careers_os.ingestion.scheduled_run import run_scheduled_pipeline
from careers_os.scoring import ai
from careers_os.storage.repository import JobRepository

import test_discovery_expansion as shared

PROFILES, FakeChannel, FakeSource, _prefs = shared.PROFILES, shared.FakeChannel, shared.FakeSource, shared._prefs
evidence_index = shared.evidence_index  # pytest fixture, reused

STRONG_REVIEW = {"role_fit_score": 0.95, "career_direction_score": 0.95, "role_fit_reason": "fits"}
WEAK_REVIEW = {
    "role_fit_score": 0.1, "career_direction_score": 0.1,
    "role_fit_reason": "Mostly account management.", "career_direction_reason": "Not a build role.",
}


class FakeClaude:
    def __init__(self, review=STRONG_REVIEW, error=None):
        self.review, self.error, self.calls = review, error, []

    def __call__(self, job, profile):
        self.calls.append(job.title)
        if self.error:
            raise self.error
        return self.review


@pytest.fixture
def claude(monkeypatch):
    fake = FakeClaude()
    monkeypatch.setattr(ai, "readiness", lambda: (True, "ready"))
    monkeypatch.setattr(ai, "evaluate", fake)
    return fake


def _catalog(n):
    return [{"id": str(i), "title": f"Lead Solutions Architect {i}", "company": f"Co{i}"} for i in range(n)]


def _prefs_with_budget(budget):
    return _prefs().model_copy(update={"ai_refinement": AIRefinementSettings(max_refinements_per_run=budget)})


def _run(db_session, evidence_index, catalog, *, budget=15, use_ai=True, applications=None):
    return run_scheduled_pipeline(
        JobRepository(db_session), [("ashby", FakeSource("ashby", catalog))], PROFILES,
        dry_run=True, channel=FakeChannel(), use_ai=use_ai, preferences=_prefs_with_budget(budget),
        evidence_index=evidence_index, applications=applications or ApplicationLog(),
    )


class TestBudget:
    def test_new_calls_never_exceed_the_budget_and_the_rest_stay_deterministic(self, db_session, evidence_index, claude):
        claude.review = WEAK_REVIEW
        result = _run(db_session, evidence_index, _catalog(5), budget=2)
        stats = result.metrics.ai_refinement
        assert (stats.status, stats.calls, stats.over_budget, stats.finalists) == ("ran", 2, 3, 5)
        assert len(claude.calls) == 2
        # 2 demoted by review, 3 unreviewed keep their deterministic strong_pursue.
        assert result.metrics.deterministic_alert_threshold_count == 5
        assert result.metrics.alert_threshold_count == 3
        assert len(stats.changed) == 2

    def test_zero_budget_makes_no_calls(self, db_session, evidence_index, claude):
        result = _run(db_session, evidence_index, _catalog(3), budget=0)
        assert claude.calls == []
        assert result.metrics.ai_refinement.over_budget == 3

    def test_missing_setting_defaults_to_a_conservative_budget(self, tmp_path):
        data = yaml.safe_load(DEFAULT_PREFERENCES_PATH.read_text())
        data.pop("ai_refinement", None)
        path = tmp_path / "prefs.yaml"
        path.write_text(yaml.safe_dump(data))
        assert Preferences.load(path).ai_refinement.max_refinements_per_run == 15

    def test_negative_budget_is_rejected(self):
        with pytest.raises(ValueError):
            AIRefinementSettings(max_refinements_per_run=-1)


class TestStoredReviews:
    def test_a_second_run_reuses_the_review_instead_of_calling_again(self, db_session, evidence_index, claude):
        claude.review = WEAK_REVIEW
        _run(db_session, evidence_index, _catalog(2))
        assert len(claude.calls) == 2
        result = _run(db_session, evidence_index, _catalog(2))
        stats = result.metrics.ai_refinement
        assert len(claude.calls) == 2
        assert (stats.calls, stats.reused) == (0, 2)
        assert result.metrics.alert_threshold_count == 0  # stored demotion still applies

    def test_changed_description_is_reviewed_again(self, db_session, evidence_index, claude):
        _run(db_session, evidence_index, _catalog(1))
        changed = [{**_catalog(1)[0], "description": "Own solution architecture on AWS with REST API "
                    "integrations and AI automation; technical leadership and customer-facing delivery. New scope."}]
        _run(db_session, evidence_index, changed)
        assert len(claude.calls) == 2

    def test_stored_reviews_apply_even_without_a_key(self, db_session, evidence_index, claude, monkeypatch):
        claude.review = WEAK_REVIEW
        _run(db_session, evidence_index, _catalog(1))
        monkeypatch.setattr(ai, "readiness", lambda: (False, "ANTHROPIC_API_KEY is not set"))
        result = _run(db_session, evidence_index, _catalog(1))
        assert result.metrics.ai_refinement.reused == 1
        assert result.metrics.alert_threshold_count == 0


class TestDemotionAndSafety:
    def test_demoted_job_is_not_alerted_and_its_reviewed_evaluation_is_saved(self, db_session, evidence_index, claude):
        claude.review = WEAK_REVIEW
        repo = JobRepository(db_session)
        result = run_scheduled_pipeline(
            repo, [("ashby", FakeSource("ashby", _catalog(1)))], PROFILES, dry_run=False,
            channel=(channel := FakeChannel()), preferences=_prefs_with_budget(5),
            evidence_index=evidence_index, applications=ApplicationLog(),
        )
        assert channel.sent == []
        assert result.alerts == []
        job = repo.get_job("ashby", "0")
        saved = repo.get_latest_evaluation(job.id)
        assert saved.pursue_recommendation != PursueRecommendation.STRONG_PURSUE.value
        assert repo.list_notifications(job.id) == []

    def test_strong_review_keeps_the_alert(self, db_session, evidence_index, claude):
        result = _run(db_session, evidence_index, _catalog(1))
        assert result.metrics.ai_refinement.calls == 1
        assert [a.status for a in result.alerts] == ["dry_run"]

    @pytest.mark.parametrize("fake", [
        FakeClaude(error=RuntimeError("overloaded")),
        FakeClaude(review={"role_fit_score": "high"}),
        FakeClaude(review={"role_fit_score": 3, "career_direction_score": 0.5}),
        FakeClaude(review=None),
    ])
    def test_failed_or_malformed_review_keeps_the_deterministic_decision(self, db_session, evidence_index, monkeypatch, fake):
        monkeypatch.setattr(ai, "readiness", lambda: (True, "ready"))
        monkeypatch.setattr(ai, "evaluate", fake)
        result = _run(db_session, evidence_index, _catalog(1))
        assert result.metrics.ai_refinement.failures == 1
        assert [a.status for a in result.alerts] == ["dry_run"]
        # A failure is not cached: the next run tries again.
        _run(db_session, evidence_index, _catalog(1))
        assert len(fake.calls) == 2

    def test_without_a_key_no_call_is_attempted(self, db_session, evidence_index, monkeypatch):
        fake = FakeClaude()
        monkeypatch.setattr(ai, "evaluate", fake)
        result = _run(db_session, evidence_index, _catalog(2))
        assert fake.calls == []
        assert result.metrics.ai_refinement.status == "no new reviews: ANTHROPIC_API_KEY is not set"
        assert result.metrics.alert_threshold_count == 2

    def test_no_ai_flag_disables_review(self, db_session, evidence_index, claude):
        result = _run(db_session, evidence_index, _catalog(2), use_ai=False)
        assert claude.calls == []
        assert result.metrics.ai_refinement.status == "disabled (--no-ai)"

    def test_applied_jobs_are_not_reviewed(self, db_session, evidence_index, claude):
        log = ApplicationLog(applications=[ApplicationEntry(company="Co0", title="Lead Solutions Architect 0")])
        result = _run(db_session, evidence_index, _catalog(2), applications=log)
        assert claude.calls == ["Lead Solutions Architect 1"]
        assert result.metrics.applications_suppressed == 1

    def test_bulk_discovery_never_calls_claude_per_job(self, db_session, evidence_index, claude):
        _run(db_session, evidence_index, _catalog(4), use_ai=False)
        assert claude.calls == []


class TestDiscoverCommandPath:
    def test_only_the_displayed_top_results_are_reviewed(self, db_session, evidence_index, claude):
        result = run_discovery(
            JobRepository(db_session), [("ashby", FakeSource("ashby", _catalog(6)))], PROFILES,
            limit=2, use_ai=True, preferences=_prefs_with_budget(15), evidence_index=evidence_index,
        )
        assert len(claude.calls) == 2
        assert result.ai_refinement.finalists == 2
        assert len(result.opportunities) == 2

    def test_refine_finalists_with_an_explicit_evaluator(self, db_session, evidence_index):
        discovered = run_discovery(
            JobRepository(db_session), [("ashby", FakeSource("ashby", _catalog(1)))], PROFILES,
            use_ai=False, preferences=_prefs(), evidence_index=evidence_index,
        )
        fake = FakeClaude(review=WEAK_REVIEW)
        stats = refine_finalists(
            discovered.opportunities, repository=JobRepository(db_session), budget=1,
            preferences=_prefs(), evidence_index=evidence_index, evaluator=fake,
        )
        assert stats.calls == 1
        assert discovered.opportunities[0].decision.pursue.recommendation != PursueRecommendation.STRONG_PURSUE
        assert discovered.opportunities[0].fit.ai_evaluation_included


class TestClaudeClientPath:
    """The real `scoring.ai.evaluate`, with the Anthropic client faked."""

    @pytest.fixture
    def fake_client(self, monkeypatch):
        import sys
        import types

        sent = []

        class Messages:
            reply = '```json\n{"role_fit_score": 0.3, "career_direction_score": 0.4}\n```'

            def create(self, **kwargs):
                sent.append(kwargs)
                block = types.SimpleNamespace(type="text", text=self.reply)
                return types.SimpleNamespace(content=[block])

        module = types.SimpleNamespace(Anthropic=lambda: types.SimpleNamespace(messages=Messages()))
        monkeypatch.setitem(sys.modules, "anthropic", module)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
        return sent

    def _job(self):
        from datetime import datetime, timezone

        from careers_os.domain.job import NormalizedJob

        return NormalizedJob(source="t", source_job_id="1", source_url="https://e.com/1",
                             title="Forward Deployed Engineer", description="Build integrations.",
                             retrieved_at=datetime.now(timezone.utc))

    def test_fenced_json_reply_is_parsed(self, fake_client):
        from careers_os.career.profile import CareerProfile

        result = ai.evaluate(self._job(), CareerProfile.load())
        assert result == {"role_fit_score": 0.3, "career_direction_score": 0.4}
        assert fake_client[0]["model"] == ai.MODEL
        assert "Two valid paths" in fake_client[0]["messages"][0]["content"]

    def test_prose_reply_is_a_failure_not_a_crash(self, fake_client, monkeypatch):
        from careers_os.career.profile import CareerProfile
        import sys

        monkeypatch.setattr(type(sys.modules["anthropic"].Anthropic().messages), "reply", "I cannot help with that.")
        assert ai.evaluate(self._job(), CareerProfile.load()) is None
