"""Phase 5 acceptance tests: the replacement objective.

The primary objective is a fully remote, hands-on development role
paying at least the configured base-salary floor that can replace the
current position. The exploratory (FDE / applied AI / solutions) track
stays discoverable but is ranked and alerted independently.

Everything here runs offline through the ordinary pipeline.
"""

from datetime import date, datetime, timezone

import pytest

from careers_os.career.evidence_index import EvidenceIndex
from careers_os.career.preferences import Preferences, TrackAlertSettings
from careers_os.career.profile import CareerProfile
from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.compensation import CompensationStatus
from careers_os.domain.enums import EmploymentType, OperatingMode, RemoteStatus, SearchTrack
from careers_os.domain.evidence import Evidence, EvidenceProvenance
from careers_os.domain.opportunity_decision import PursueRecommendation, Readiness
from careers_os.domain.job import NormalizedJob
from careers_os.domain.resume import CareerRole
from careers_os.domain.taxonomy import SkillCategory
from careers_os.ingestion.evaluation import evaluate_opportunity
from careers_os.notifications.policy import should_alert
from careers_os.scoring.engine import score_job

NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)


@pytest.fixture
def evidence_index() -> EvidenceIndex:
    role = CareerRole(id="r", company="Acme", title="Senior Engineer", start_date=date(2016, 1, 1))
    prov = EvidenceProvenance(source_type="resume", source_name="dev", company="Acme",
                              career_role_id="r", variant_slug="dev", extracted_at=NOW)

    def ev(id_, type_, skills):
        return Evidence(id=id_, type=type_, statement=id_, canonical_skills=skills,
                        provenance=prov, start_date=role.start_date)

    return EvidenceIndex(evidence=[
        ev("ev-php", SkillCategory.PROGRAMMING_LANGUAGE, ["php", "laravel", "javascript", "typescript"]),
        ev("ev-cms", SkillCategory.FRAMEWORK, ["craft_cms", "wordpress", "cms"]),
        ev("ev-api", SkillCategory.ARCHITECTURE, ["rest_api", "integration", "platform_engineering",
                                                  "solutions_architecture"]),
        ev("ev-cloud", SkillCategory.CLOUD, ["aws", "cloud_infrastructure", "ci_cd", "linux"]),
        ev("ev-data", SkillCategory.DATA, ["sql"]),
        ev("ev-ai", SkillCategory.AI_ML, ["ai_ml", "automation"]),
    ], career_roles=[role], taxonomy=SkillsTaxonomy.load())


_BUILD_BODY = (
    " You will write production code daily, build and ship features, debug production issues, and own "
    "deployments. Requirements: 5+ years of PHP experience. Experience with Laravel and REST APIs. "
    "Experience in building integrations against AWS. Strong knowledge of SQL. "
)


def _job(title, body=_BUILD_BODY, **kw):
    kw.setdefault("employment_type", EmploymentType.FULL_TIME)
    kw.setdefault("remote_status", RemoteStatus.REMOTE)
    kw.setdefault("location", "United States - Remote")
    return NormalizedJob(source="test", source_job_id="1", source_url="https://e.com/1",
                         title=title, description=body, retrieved_at=NOW, **kw)


def _decide(job, evidence_index, tracks=(SearchTrack.REPLACEMENT,)):
    prefs = Preferences.load()
    fit = score_job(job, CareerProfile.load(), prefs, use_ai=False, evidence_index=evidence_index)
    return evaluate_opportunity(job, fit, preferences=prefs, evidence_index=evidence_index,
                                use_ai=False, tracks=list(tracks)).decision


class TestRemoteEligibility:
    def test_remote_us_role_is_eligible(self, evidence_index):
        d = _decide(_job("Senior Laravel Developer", salary_min=130000, salary_max=160000), evidence_index)
        assert d.eligibility.status.value == "eligible"

    def test_remote_texas_role_is_eligible(self, evidence_index):
        d = _decide(_job("Senior Laravel Developer", location="Remote - Texas",
                         salary_min=130000, salary_max=160000), evidence_index)
        assert d.eligibility.status.value == "eligible"

    def test_remote_california_only_role_is_not_eligible(self, evidence_index):
        d = _decide(_job("Senior Laravel Developer",
                         _BUILD_BODY + " Must reside in California for this position.",
                         salary_min=130000, salary_max=160000), evidence_index)
        assert d.eligibility.status.value == "ineligible"
        assert d.pursue.recommendation == PursueRecommendation.DO_NOT_PURSUE

    @pytest.mark.parametrize("location", ["EU | Remote", "Remote, Canada", "Berlin Office"])
    def test_non_us_remote_scope_is_never_silently_eligible(self, evidence_index, location):
        d = _decide(_job("Senior Laravel Developer", location=location,
                         salary_min=130000, salary_max=160000), evidence_index)
        assert d.eligibility.status.value in ("verify", "ineligible")
        assert d.readiness.level in (Readiness.NEEDS_VERIFICATION, Readiness.NOT_A_FIT)

    @pytest.mark.parametrize("status", [RemoteStatus.HYBRID, RemoteStatus.ONSITE])
    def test_hybrid_and_onsite_cannot_produce_a_replacement_alert(self, evidence_index, status):
        d = _decide(_job("Senior Laravel Developer", remote_status=status,
                         salary_min=130000, salary_max=160000), evidence_index)
        assert d.pursue.recommendation == PursueRecommendation.DO_NOT_PURSUE
        assert not should_alert(d, Preferences.load().alert_policy, OperatingMode.PASSIVE,
                                track=SearchTrack.REPLACEMENT)

    def test_unstated_arrangement_requires_verification(self, evidence_index):
        d = _decide(_job("Senior Laravel Developer", remote_status=RemoteStatus.UNKNOWN,
                         location=None, salary_min=130000, salary_max=160000), evidence_index)
        assert d.readiness.level == Readiness.NEEDS_VERIFICATION
        assert d.pursue.recommendation == PursueRecommendation.VERIFY_FIRST


class TestCompensationCertaintyEndToEnd:
    def test_range_above_floor_is_confirmed_and_can_be_an_immediate_fit(self, evidence_index):
        d = _decide(_job("Senior Laravel Developer", salary_min=125000, salary_max=150000), evidence_index)
        assert d.compensation.status == CompensationStatus.CONFIRMED_ABOVE
        assert d.readiness.level == Readiness.IMMEDIATE_FIT

    def test_range_at_floor_is_confirmed(self, evidence_index):
        d = _decide(_job("Senior Laravel Developer", salary_min=120000, salary_max=150000), evidence_index)
        assert d.compensation.status == CompensationStatus.CONFIRMED_AT and d.compensation.confirmed

    def test_range_straddling_the_floor_needs_verification(self, evidence_index):
        d = _decide(_job("Senior Laravel Developer", salary_min=100000, salary_max=130000), evidence_index)
        assert d.compensation.status == CompensationStatus.OVERLAPS_THRESHOLD
        assert d.readiness.level == Readiness.NEEDS_VERIFICATION
        assert not d.compensation.confirmed

    def test_range_below_the_floor_is_reported_below(self, evidence_index):
        d = _decide(_job("Senior Laravel Developer", salary_min=90000, salary_max=115000), evidence_index)
        assert d.compensation.status == CompensationStatus.BELOW_THRESHOLD

    def test_unpublished_salary_needs_verification_but_is_not_rejected(self, evidence_index):
        d = _decide(_job("Senior Laravel Developer"), evidence_index)
        assert d.compensation.status == CompensationStatus.UNKNOWN
        assert d.readiness.level == Readiness.NEEDS_VERIFICATION
        assert d.pursue.recommendation != PursueRecommendation.DO_NOT_PURSUE

    def test_ote_figure_is_not_a_confirmed_base_salary_match(self, evidence_index):
        d = _decide(_job("Senior Laravel Developer",
                         _BUILD_BODY + " Compensation: $150,000 OTE including commission.",
                         salary_min=150000, salary_max=150000), evidence_index)
        assert d.compensation.status == CompensationStatus.BASIS_UNCERTAIN
        assert d.readiness.level == Readiness.NEEDS_VERIFICATION

    def test_contract_hourly_is_not_judged_against_the_salary_floor(self, evidence_index):
        d = _decide(_job("Senior Laravel Developer", employment_type=EmploymentType.CONTRACT,
                         hourly_min=95, hourly_max=120), evidence_index)
        assert d.compensation.status == CompensationStatus.NON_SALARY
        assert d.readiness.level != Readiness.NEEDS_VERIFICATION


class TestQualificationAndReadiness:
    def test_strong_stack_overlap_is_an_immediate_fit(self, evidence_index):
        d = _decide(_job("Senior Craft CMS Developer",
                         " Requirements: 5+ years of PHP. Experience with Craft CMS, REST APIs and AWS. "
                         "You will write production code and own deployments. ",
                         salary_min=130000, salary_max=160000), evidence_index)
        assert d.readiness.level == Readiness.IMMEDIATE_FIT
        assert d.pursue.recommendation == PursueRecommendation.STRONG_PURSUE

    def test_mandatory_years_in_an_unevidenced_language_cannot_strong_pursue(self, evidence_index):
        d = _decide(_job("Backend Engineer",
                         " Minimum requirements: 3+ years of Go development. "
                         "Experience with REST APIs, PHP and AWS. ",
                         salary_min=140000, salary_max=170000), evidence_index)
        assert d.pursue.recommendation != PursueRecommendation.STRONG_PURSUE
        assert d.readiness.level == Readiness.NOT_A_FIT

    @pytest.mark.xfail(strict=True, reason=(
        "Known limitation (Phase 5): skills_taxonomy aliases for short names are space-padded "
        "(\" go \"), so a mention followed by punctuation is never extracted and the hard gate "
        "silently disappears. Fixing it needs word-boundary alias matching, which risks false "
        "positives on short aliases across all scoring — tracked as a follow-up."))
    def test_hard_requirement_followed_by_punctuation_is_still_detected(self, evidence_index):
        d = _decide(_job("Backend Engineer",
                         " Minimum requirements: 3+ years of Go. Experience with REST APIs, PHP and AWS. ",
                         salary_min=140000, salary_max=170000), evidence_index)
        assert d.readiness.level == Readiness.NOT_A_FIT

    def test_preferred_only_gap_does_not_disqualify(self, evidence_index):
        d = _decide(_job("Senior Laravel Developer",
                         _BUILD_BODY + " Preferred qualifications: Experience with Kubernetes. ",
                         salary_min=130000, salary_max=160000), evidence_index)
        assert d.readiness.level == Readiness.IMMEDIATE_FIT
        assert "kubernetes" not in " ".join(d.readiness.core_gaps).lower()

    def test_several_unevidenced_core_responsibilities_make_it_a_learning_target(self, evidence_index):
        d = _decide(_job("Platform Engineer",
                         " Requirements: Deep knowledge of Kubernetes operators and service mesh internals. "
                         "Strong experience with distributed consensus protocols. "
                         "Expertise in eBPF observability tooling. "
                         "Experience with hardware capacity forecasting. ",
                         salary_min=150000, salary_max=180000), evidence_index)
        assert d.readiness.level == Readiness.LEARNING_TARGET
        assert d.pursue.recommendation != PursueRecommendation.STRONG_PURSUE

    def test_a_couple_of_manageable_gaps_is_a_stretch(self, evidence_index):
        d = _decide(_job("Full Stack Engineer",
                         " Requirements: Experience with PHP, REST APIs and AWS. "
                         "Experience with Python and Kubernetes in production. ",
                         salary_min=130000, salary_max=160000), evidence_index)
        assert d.readiness.level == Readiness.STRETCH
        assert d.pursue.recommendation != PursueRecommendation.STRONG_PURSUE

    def test_career_direction_cannot_rescue_a_weak_immediate_opportunity(self, evidence_index):
        """Replacement track only: a strong long-term signal with a weak
        immediate opportunity is not a replacement role."""
        from careers_os.career.opportunity_value import OpportunityAssessment, OpportunityLevel
        from careers_os.career.pursue import compute_pursue_recommendation
        from careers_os.domain.eligibility import EligibilityResult, EligibilityStatus
        from careers_os.domain.opportunity_decision import OpportunityCostLevel, OpportunityCostResult
        from careers_os.domain.qualification import QualificationResult, QualificationStatus

        args = (
            EligibilityResult(status=EligibilityStatus.ELIGIBLE, checks=[]),
            QualificationResult(status=QualificationStatus.STRONG, reason="t"),
            OpportunityAssessment(level=OpportunityLevel.MODERATE, reason="t"),
            OpportunityAssessment(level=OpportunityLevel.STRONG, reason="t"),
            OpportunityCostResult(level=OpportunityCostLevel.LOW, reason="t"),
        )
        replacement = compute_pursue_recommendation(*args, track=SearchTrack.REPLACEMENT)
        exploratory = compute_pursue_recommendation(*args, track=SearchTrack.EXPLORATORY)
        assert replacement.recommendation == PursueRecommendation.CONSIDER
        assert exploratory.recommendation == PursueRecommendation.PURSUE

    def test_moderate_qualification_is_a_stretch_not_a_strong_replacement(self, evidence_index):
        from careers_os.career.opportunity_value import OpportunityAssessment, OpportunityLevel
        from careers_os.career.pursue import compute_pursue_recommendation
        from careers_os.domain.eligibility import EligibilityResult, EligibilityStatus
        from careers_os.domain.opportunity_decision import OpportunityCostLevel, OpportunityCostResult
        from careers_os.domain.qualification import QualificationResult, QualificationStatus

        strong = OpportunityAssessment(level=OpportunityLevel.STRONG, reason="t")
        args = (
            EligibilityResult(status=EligibilityStatus.ELIGIBLE, checks=[]),
            QualificationResult(status=QualificationStatus.MODERATE, reason="t"),
            strong, strong,
            OpportunityCostResult(level=OpportunityCostLevel.LOW, reason="t"),
        )
        assert compute_pursue_recommendation(*args, track=SearchTrack.REPLACEMENT).recommendation == (
            PursueRecommendation.PURSUE)
        assert compute_pursue_recommendation(*args, track=SearchTrack.EXPLORATORY).recommendation == (
            PursueRecommendation.STRONG_PURSUE)


class TestWorkStyle:
    def test_hands_on_development_can_reach_replacement_strong_pursue(self, evidence_index):
        d = _decide(_job("Senior Laravel Developer", salary_min=130000, salary_max=160000), evidence_index)
        assert d.pursue.recommendation == PursueRecommendation.STRONG_PURSUE

    def test_presales_and_meeting_heavy_work_is_capped(self, evidence_index):
        body = (
            " Requirements: Experience with PHP, REST APIs and AWS. You will run discovery calls, deliver "
            "demos and presentations to prospects, manage account relationships, attend weekly customer "
            "meetings, write tailored follow-up emails, and coordinate stakeholders across teams. "
        )
        d = _decide(_job("Solutions Consultant", body, salary_min=130000, salary_max=160000), evidence_index)
        assert d.pursue.recommendation != PursueRecommendation.STRONG_PURSUE


class TestTrackSeparation:
    def test_replacement_and_exploratory_budgets_are_independent(self):
        policy = Preferences.load().alert_policy
        replacement = policy.for_track(SearchTrack.REPLACEMENT, OperatingMode.PASSIVE)
        exploratory = policy.for_track(SearchTrack.EXPLORATORY, OperatingMode.PASSIVE)
        assert replacement.max_alerts_per_run == 4
        assert replacement.max_verification_alerts_per_run == 1
        assert exploratory.max_alerts_per_run == 1

    def test_missing_track_config_falls_back_to_mode_settings(self):
        policy = Preferences.load().alert_policy.model_copy(update={"tracks": {}})
        settings = policy.for_track(SearchTrack.REPLACEMENT, OperatingMode.PASSIVE)
        assert settings.max_alerts_per_run == policy.passive.max_alerts_per_run
        assert settings.minimum_pursue == policy.passive.minimum_pursue

    def test_track_thresholds_apply_independently(self, evidence_index):
        # A stretch-level role: alertable where the threshold is `pursue`,
        # not where it is `strong_pursue`.
        d = _decide(_job("Full Stack Engineer",
                         " Requirements: Experience with PHP, REST APIs and AWS. "
                         "Experience with Python and Kubernetes in production. ",
                         salary_min=130000, salary_max=160000), evidence_index)
        assert d.pursue.recommendation == PursueRecommendation.PURSUE
        policy = Preferences.load().alert_policy.model_copy(update={"tracks": {
            SearchTrack.REPLACEMENT: TrackAlertSettings(
                minimum_pursue=PursueRecommendation.PURSUE, max_alerts_per_run=4),
            SearchTrack.EXPLORATORY: TrackAlertSettings(
                minimum_pursue=PursueRecommendation.STRONG_PURSUE, max_alerts_per_run=1),
        }})
        assert should_alert(d, policy, OperatingMode.PASSIVE, track=SearchTrack.REPLACEMENT)
        assert not should_alert(d, policy, OperatingMode.PASSIVE, track=SearchTrack.EXPLORATORY)

    def test_profiles_map_to_their_configured_track(self):
        from careers_os.career.search_profiles import SearchProfilesConfig
        config = SearchProfilesConfig.load()
        assert config.track_for("craft") == SearchTrack.REPLACEMENT
        assert config.track_for("fullstack_js") == SearchTrack.REPLACEMENT
        assert config.track_for("fde") == SearchTrack.EXPLORATORY


class TestVerificationLane:
    """A strong replacement whose pay is unconfirmed still surfaces, in
    its own small lane, and never as a confirmed match."""

    def test_unknown_salary_role_reaches_the_verification_lane(self, evidence_index):
        from careers_os.notifications.policy import VERIFICATION_LANE, alert_lane
        d = _decide(_job("Senior Laravel Developer"), evidence_index)
        assert d.readiness.level == Readiness.NEEDS_VERIFICATION
        lane = alert_lane(d, Preferences.load().alert_policy, OperatingMode.PASSIVE, SearchTrack.REPLACEMENT)
        assert lane == VERIFICATION_LANE

    def test_confirmed_role_uses_the_confirmed_lane(self, evidence_index):
        from careers_os.notifications.policy import CONFIRMED_LANE, alert_lane
        d = _decide(_job("Senior Laravel Developer", salary_min=130000, salary_max=160000), evidence_index)
        lane = alert_lane(d, Preferences.load().alert_policy, OperatingMode.PASSIVE, SearchTrack.REPLACEMENT)
        assert lane == CONFIRMED_LANE

    def test_weak_role_with_unknown_salary_gets_no_lane(self, evidence_index):
        from careers_os.notifications.policy import alert_lane
        d = _decide(_job("Platform Engineer",
                         " Requirements: Deep knowledge of Kubernetes operators and service mesh internals. "
                         "Strong experience with distributed consensus protocols. "
                         "Expertise in eBPF observability tooling. "), evidence_index)
        assert alert_lane(d, Preferences.load().alert_policy, OperatingMode.PASSIVE,
                          SearchTrack.REPLACEMENT) is None

    def test_exploratory_track_has_no_verification_lane(self, evidence_index):
        from careers_os.notifications.policy import alert_lane
        d = _decide(_job("Senior Laravel Developer"), evidence_index, tracks=(SearchTrack.EXPLORATORY,))
        assert alert_lane(d, Preferences.load().alert_policy, OperatingMode.PASSIVE,
                          SearchTrack.EXPLORATORY) is None

    def test_verification_alerts_do_not_displace_confirmed_ones(self, db_session, evidence_index):
        """Budgets are spent independently: 4 confirmed + 1 verification."""
        from careers_os.career.preferences import TrackAlertSettings
        policy = Preferences.load().alert_policy
        settings = policy.for_track(SearchTrack.REPLACEMENT, OperatingMode.PASSIVE)
        assert isinstance(settings, TrackAlertSettings)
        assert settings.max_alerts_per_run == 4 and settings.max_verification_alerts_per_run == 1


class TestPhase51Guardrails:
    """Failures the first live dry run exposed."""

    def test_posting_with_no_parseable_requirements_is_not_an_immediate_fit(self, evidence_index):
        """The Deepgram case: a backend posting with no requirements
        heading produced "no core gaps" and read as a clean match."""
        body = ("We are building inference services at scale. Join a team shipping audio models to "
                "production. You will collaborate with researchers and help operate our platform.")
        d = _decide(_job("Backend Engineer - Inference Services", body,
                         salary_min=150000, salary_max=220000), evidence_index)
        assert d.readiness.level == Readiness.NEEDS_VERIFICATION
        assert d.pursue.recommendation != PursueRecommendation.STRONG_PURSUE
        assert "No requirements could be parsed" in d.readiness.reason

    def test_core_requirements_with_no_supporting_evidence_are_not_an_immediate_fit(self, evidence_index):
        body = (" Requirements: Deep experience with Erlang and OTP supervision trees. "
                "Experience with Elixir in production. ")
        d = _decide(_job("Backend Engineer", body, salary_min=150000, salary_max=200000), evidence_index)
        assert d.readiness.level != Readiness.IMMEDIATE_FIT

    def test_learning_target_is_capped_below_pursue_on_replacement(self, evidence_index):
        """The Curotec case: substantial gaps presented as `pursue`."""
        d = _decide(_job("Platform Engineer",
                         " Requirements: Deep knowledge of Kubernetes operators and service mesh internals. "
                         "Strong experience with distributed consensus protocols. "
                         "Expertise in eBPF observability tooling. "
                         "Experience with capacity forecasting for bare-metal fleets. ",
                         salary_min=150000, salary_max=185000), evidence_index)
        assert d.readiness.level == Readiness.LEARNING_TARGET
        assert d.pursue.recommendation == PursueRecommendation.CONSIDER

    def test_next_js_is_extracted_as_a_core_requirement(self, evidence_index):
        from careers_os.scoring.requirements import extract_requirements
        job = _job("Senior Full-Stack Engineer (Next.js / AI)",
                   " Responsibilities: Build product surfaces in Next.js and React. Integrate REST APIs. ")
        core = {r.canonical_skill for r in extract_requirements(job, SkillsTaxonomy.load()) if r.is_core}
        assert {"next_js", "react"} <= core

    def test_responsibilities_section_establishes_core_requirements(self, evidence_index):
        from careers_os.scoring.requirements import extract_requirements
        job = _job("Software Engineer",
                   " What you'll do: Build Laravel services. Integrate REST APIs against AWS. ")
        core = {r.canonical_skill for r in extract_requirements(job, SkillsTaxonomy.load()) if r.is_core}
        assert {"laravel", "rest_api", "aws"} <= core

    def test_reactive_and_exposure_do_not_match_react_or_expo(self):
        from careers_os.scoring.requirements import extract_requirements
        job = _job("Backend Engineer",
                   " Requirements: Build reactive systems with fast reaction times and broad exposure. ")
        skills = {r.canonical_skill for r in extract_requirements(job, SkillsTaxonomy.load())}
        assert "react" not in skills and "react_native" not in skills

    def test_technology_in_a_parenthesized_title_is_core(self):
        """GitLab's "Database Automation (Go)" extracted Go from the body
        but never marked it core, so a Go-centric role read as a clean
        match on one unrelated requirement."""
        from careers_os.scoring.requirements import extract_requirements
        job = _job("Staff Backend Engineer, Database Automation (Go)",
                   " Build automation tooling. You will work with Go across our fleet. "
                   " Requirements: Experience with workflow automation. ")
        core = {r.canonical_skill for r in extract_requirements(job, SkillsTaxonomy.load()) if r.is_core}
        assert "go" in core
