from datetime import datetime, timezone

from careers_os.career.preferences import Preferences
from careers_os.career.profile import CareerProfile
from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.job import NormalizedJob
from careers_os.scoring import deterministic
from careers_os.scoring.engine import score_job


def _job(**overrides) -> NormalizedJob:
    defaults = dict(
        source="creative_circle",
        source_job_id="1",
        source_url="https://example.com/1",
        title="Solutions Architect",
        employment_type=EmploymentType.FULL_TIME,
        remote_status=RemoteStatus.REMOTE,
        retrieved_at=datetime.now(timezone.utc),
        description="Own solution architecture and build customer-specific integrations "
        "using APIs and cloud infrastructure.",
    )
    defaults.update(overrides)
    return NormalizedJob(**defaults)


class TestRoleFit:
    def test_title_match_scores_higher_than_body_only_match(self):
        profile = CareerProfile.load()
        titled = _job(title="Solutions Architect")
        untitled = _job(title="Marketing Coordinator")
        titled_score = deterministic.score_role_fit(titled, profile)
        untitled_score = deterministic.score_role_fit(untitled, profile)
        assert titled_score.score > untitled_score.score

    def test_no_match_scores_zero_with_reason(self):
        profile = CareerProfile.load()
        job = _job(title="Graphic Designer", description="Design social media graphics.")
        result = deterministic.score_role_fit(job, profile)
        assert result.score == 0.0
        assert "No target-role" in result.reason


class TestCompensationFit:
    def test_missing_salary_is_neutral_not_penalized(self):
        prefs = Preferences.load()
        job = _job(employment_type=EmploymentType.FULL_TIME, salary_min=None, salary_max=None)
        result = deterministic.score_compensation_fit(job, prefs)
        assert result.score == 0.5
        assert result.confidence < 0.3

    def test_salary_above_strong_threshold_scores_max(self):
        prefs = Preferences.load()
        job = _job(employment_type=EmploymentType.FULL_TIME, salary_min=210000, salary_max=230000)
        result = deterministic.score_compensation_fit(job, prefs)
        assert result.score == 1.0

    def test_salary_below_minimum_scores_low_but_not_zero_penalty_free(self):
        prefs = Preferences.load()
        job = _job(employment_type=EmploymentType.FULL_TIME, salary_min=100000, salary_max=120000)
        result = deterministic.score_compensation_fit(job, prefs)
        assert 0.0 <= result.score < 0.6

    def test_hourly_does_not_get_compared_against_salary_thresholds(self):
        prefs = Preferences.load()
        job = _job(
            employment_type=EmploymentType.FREELANCE,
            hourly_min=90,
            hourly_max=110,
            salary_min=None,
            salary_max=None,
        )
        result = deterministic.score_compensation_fit(job, prefs)
        assert "hourly" in result.reason.lower()
        assert result.score > 0.5

    def test_missing_hourly_for_contract_role_is_neutral(self):
        prefs = Preferences.load()
        job = _job(employment_type=EmploymentType.CONTRACT, hourly_min=None, hourly_max=None)
        result = deterministic.score_compensation_fit(job, prefs)
        assert result.score == 0.5
        assert result.confidence < 0.3


class TestWorkArrangementFit:
    def test_remote_scores_highest(self):
        prefs = Preferences.load()
        remote = deterministic.score_work_arrangement_fit(_job(remote_status=RemoteStatus.REMOTE), prefs)
        hybrid = deterministic.score_work_arrangement_fit(_job(remote_status=RemoteStatus.HYBRID), prefs)
        onsite = deterministic.score_work_arrangement_fit(_job(remote_status=RemoteStatus.ONSITE), prefs)
        assert remote.score > hybrid.score > onsite.score

    def test_unknown_remote_status_is_neutral_low_confidence(self):
        prefs = Preferences.load()
        result = deterministic.score_work_arrangement_fit(
            _job(remote_status=RemoteStatus.UNKNOWN), prefs
        )
        assert result.score == 0.5
        assert result.confidence < 0.3


class TestExperienceFit:
    def test_always_neutral_with_low_confidence_in_phase_1(self):
        result = deterministic.score_experience_fit(_job())
        assert result.score == 0.5
        assert result.confidence < 0.2


class TestScoreJobEngine:
    def test_overall_fit_is_weighted_combination_within_bounds(self):
        profile = CareerProfile.load()
        prefs = Preferences.load()
        job = _job(salary_min=190000, salary_max=210000)
        result = score_job(job, profile, prefs, use_ai=False)
        assert 0.0 <= result.overall_fit <= 1.0
        assert result.ai_evaluation_included is False

    def test_runs_without_ai_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        profile = CareerProfile.load()
        prefs = Preferences.load()
        job = _job()
        result = score_job(job, profile, prefs, use_ai=True)
        assert result.ai_evaluation_included is False
