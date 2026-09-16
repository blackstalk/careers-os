from datetime import datetime, timezone

from careers_os.career.preferences import Preferences
from careers_os.career.profile import CareerProfile
from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.experience import ExperienceFitDetail
from careers_os.domain.job import NormalizedJob
from careers_os.domain.matching import MatchType, RequirementMatch
from careers_os.domain.requirements import JobRequirement
from careers_os.domain.taxonomy import SkillCategory
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


def _requirement_match(category: SkillCategory, match_type: MatchType, skill: str = "x") -> RequirementMatch:
    req = JobRequirement(id=skill, category=category, canonical_skill=skill, text=skill)
    return RequirementMatch(requirement=req, match_type=match_type, reason="test")


class TestCareerDirectionFitEvidenceGrounding:
    """Phase 4.1 — career_direction_fit must reflect real candidate
    evidence overlap when available, not just job-text keyword presence,
    so a target-direction title alone can't override weak evidence. See
    docs/scoring.md#career-direction-fit.
    """

    def test_falls_back_to_keyword_heuristic_when_no_experience_detail(self):
        # No resume imported (experience_detail=None) — must behave
        # exactly like the pre-Phase-4.1 keyword-only heuristic.
        profile = CareerProfile.load()
        job = _job(title="Applied AI Engineer", description="Forward deployed AI engineer role.")
        result = deterministic.score_career_direction_fit(job, profile, None)
        assert result.score == deterministic.score_career_direction_fit(job, profile).score

    def test_falls_back_to_keyword_heuristic_when_no_forward_category_matches(self):
        # A resume was imported, but this specific job's extracted
        # requirements have nothing in a forward-direction category —
        # same fallback as the no-resume case.
        profile = CareerProfile.load()
        job = _job(title="Applied AI Engineer", description="Forward deployed AI engineer role.")
        detail = ExperienceFitDetail(
            requirement_matches=[_requirement_match(SkillCategory.PROGRAMMING_LANGUAGE, MatchType.STRONG_MATCH)]
        )
        with_detail = deterministic.score_career_direction_fit(job, profile, detail)
        without_detail = deterministic.score_career_direction_fit(job, profile, None)
        assert with_detail.score == without_detail.score

    def test_target_direction_title_with_unsupported_evidence_scores_low(self):
        # The exact "title similarity overpowering weak evidence" case:
        # an Applied AI Engineer posting whose forward-direction
        # requirements the candidate has NO real evidence for must score
        # low here, regardless of how many AI/FDE buzzwords are in the
        # text (which would otherwise saturate the old keyword score).
        profile = CareerProfile.load()
        job = _job(
            title="Applied AI Engineer",
            description="Forward deployed AI engineer building production ML systems, "
            "solutions architecture, and customer-facing AI implementations.",
        )
        detail = ExperienceFitDetail(
            requirement_matches=[
                _requirement_match(SkillCategory.AI_ML, MatchType.UNSUPPORTED, "production_ml"),
                _requirement_match(SkillCategory.ARCHITECTURE, MatchType.UNSUPPORTED, "solution_architecture"),
            ]
        )
        result = deterministic.score_career_direction_fit(job, profile, detail)
        assert result.score < 0.35
        assert "real evidence overlap" in result.reason.lower() or "evidence" in result.reason.lower()

    def test_target_direction_title_with_strong_evidence_scores_high(self):
        # Path B: a target-direction role IS a credible transition when
        # the candidate has real (non-adjacent) evidence in forward
        # categories — no PHP/Laravel/Craft/WordPress needed at all.
        profile = CareerProfile.load()
        job = _job(
            title="Applied AI Engineer",
            description="Forward deployed AI engineer building production ML systems.",
        )
        detail = ExperienceFitDetail(
            requirement_matches=[
                _requirement_match(SkillCategory.AI_ML, MatchType.STRONG_MATCH, "ai_ml"),
                _requirement_match(SkillCategory.ARCHITECTURE, MatchType.STRONG_MATCH, "architecture"),
                _requirement_match(SkillCategory.CLOUD, MatchType.STRONG_MATCH, "aws"),
            ]
        )
        result = deterministic.score_career_direction_fit(job, profile, detail)
        assert result.score >= 0.9

    def test_stack_adjacent_role_with_no_direction_keywords_still_uses_evidence(self):
        # Path A: a plain PHP/Laravel posting that also happens to carry
        # real architecture-category evidence (e.g. it includes system-
        # design responsibilities) should score on that evidence, not be
        # dragged to zero just because it lacks FDE/AI terminology.
        profile = CareerProfile.load()
        job = _job(
            title="Senior Laravel Platform Engineer",
            description="Own our Laravel/PHP platform architecture and API integrations.",
        )
        detail = ExperienceFitDetail(
            requirement_matches=[
                _requirement_match(SkillCategory.ARCHITECTURE, MatchType.STRONG_MATCH, "architecture"),
                _requirement_match(SkillCategory.FRAMEWORK, MatchType.STRONG_MATCH, "laravel"),
            ]
        )
        result = deterministic.score_career_direction_fit(job, profile, detail)
        assert result.score >= 0.9

    def test_provider_and_company_identity_never_affect_the_score(self):
        # Same evidence, same job content, different source/company —
        # must score identically. Company prestige has no representation
        # anywhere in this function's inputs.
        profile = CareerProfile.load()
        detail = ExperienceFitDetail(
            requirement_matches=[_requirement_match(SkillCategory.AI_ML, MatchType.STRONG_MATCH)]
        )
        openai_job = _job(source="ashby", title="Applied AI Engineer", description="AI engineering role.")
        unknown_co_job = _job(source="greenhouse", title="Applied AI Engineer", description="AI engineering role.")
        result_a = deterministic.score_career_direction_fit(openai_job, profile, detail)
        result_b = deterministic.score_career_direction_fit(unknown_co_job, profile, detail)
        assert result_a.score == result_b.score


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
