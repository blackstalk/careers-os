"""Phase 4.3 — career-track relevance and precision regressions.

The postings below are synthetic but shaped like the false positives seen
in the Phase 4.2 dry run (React Native, SAP Commerce, program/account
managers, interns): each mentions APIs, AWS, AI, and integrations the
candidate can back up, which is exactly what used to push them to
strong_pursue. They run through the ordinary pipeline, offline.
"""

from datetime import date, datetime, timezone

import pytest

from careers_os.career.career_track import classify_career_track
from careers_os.career.evidence_index import EvidenceIndex
from careers_os.career.opportunity_value import OpportunityAssessment, OpportunityLevel
from careers_os.career.preferences import Preferences
from careers_os.career.profile import CareerProfile
from careers_os.career.pursue import compute_pursue_recommendation
from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.eligibility import EligibilityResult, EligibilityStatus
from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.evidence import Evidence, EvidenceProvenance
from careers_os.domain.experience import ExperienceFitDetail
from careers_os.domain.job import NormalizedJob
from careers_os.domain.matching import MatchType, RequirementMatch
from careers_os.domain.opportunity_decision import (
    CareerTrack,
    CareerTrackResult,
    OpportunityCostLevel,
    OpportunityCostResult,
    PursueRecommendation,
    TrackAlignment,
)
from careers_os.domain.qualification import QualificationResult, QualificationStatus
from careers_os.domain.requirements import JobRequirement, RequirementImportance
from careers_os.domain.resume import CareerRole
from careers_os.domain.taxonomy import SkillCategory
from careers_os.ingestion.evaluation import evaluate_opportunity
from careers_os.scoring.deterministic import score_technical_fit
from careers_os.scoring.engine import score_job

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)

_CATEGORY = {
    "php": SkillCategory.PROGRAMMING_LANGUAGE, "ai_ml": SkillCategory.AI_ML,
    "rest_api": SkillCategory.ARCHITECTURE, "integration": SkillCategory.ARCHITECTURE,
    "aws": SkillCategory.CLOUD, "automation": SkillCategory.ARCHITECTURE,
}


def _detail(*skills: str, match=MatchType.STRONG_MATCH) -> ExperienceFitDetail:
    return ExperienceFitDetail(requirement_matches=[
        RequirementMatch(
            requirement=JobRequirement(id=s, category=_CATEGORY[s], canonical_skill=s, text=s,
                                       importance=RequirementImportance.REQUIRED),
            match_type=match, reason="test",
        )
        for s in skills
    ])


class TestClassifyCareerTrack:
    @pytest.mark.parametrize("title", [
        "Senior React Native Developer",
        "Sr. SAP Commerce Platform Engineer",
        "Embedded AI Engineer, On-Device Models",
        "Senior Frontend Platform Engineer, Design Systems",
        "Technical Deployment Lead, Semiconductors - Texas",
        "AI/ML Data Scientist",
        "Senior Technical Program Manager, Services Tools",
        "Technical Account Manager (US)",
        "Director, AI Platform Engineer - Remote",
        "Forward Deployed Engineering Intern",
        "Associate Forward Deployed Engineer",
        "Senior Field Engineer (Pre-Sales/Forward Deployed)",
        "Appian Solution Architect",
    ])
    def test_title_defined_unrelated_role_is_off_track_regardless_of_evidence(self, title):
        result = classify_career_track(title, _detail("php", "rest_api", "integration", "aws", "ai_ml"))
        assert result.alignment == TrackAlignment.OFF_TRACK

    def test_senior_associate_is_a_level_label_not_junior(self):
        result = classify_career_track("Senior Associate - Cloud Platform Engineer", _detail("aws", "integration"))
        assert result.alignment == TrackAlignment.ALIGNED

    def test_fde_with_systems_evidence_is_aligned_career_direction(self):
        result = classify_career_track("Senior Forward Deployed Engineer", _detail("rest_api", "integration"))
        assert (result.track, result.alignment) == (CareerTrack.CAREER_DIRECTION, TrackAlignment.ALIGNED)

    def test_ai_title_without_ai_evidence_is_unclear(self):
        result = classify_career_track("Senior AI Engineer", _detail("rest_api", "integration", "aws"))
        assert result.alignment == TrackAlignment.UNCLEAR

    def test_ai_title_with_only_adjacent_evidence_is_unclear(self):
        result = classify_career_track("Applied AI Engineer", _detail("ai_ml", "rest_api", match=MatchType.ADJACENT_EXPERIENCE))
        assert result.alignment == TrackAlignment.UNCLEAR

    def test_php_title_with_stack_and_systems_evidence_is_aligned_stack_adjacent(self):
        result = classify_career_track("Senior Laravel Developer", _detail("php", "rest_api"))
        assert (result.track, result.alignment) == (CareerTrack.STACK_ADJACENT, TrackAlignment.ALIGNED)

    def test_neutral_title_is_unclear_even_with_evidence(self):
        result = classify_career_track("Industry Principal", _detail("php", "rest_api", "integration", "ai_ml"))
        assert result.alignment == TrackAlignment.UNCLEAR

    def test_technology_mentioned_only_in_description_does_not_make_it_off_track(self):
        # The title defines the job; an FDE posting mentioning React or
        # SAP somewhere in its body is not a React/SAP specialist role.
        result = classify_career_track("Forward Deployed Engineer", _detail("rest_api", "integration"))
        assert result.alignment != TrackAlignment.OFF_TRACK


class TestPursueCaps:
    def _pursue(self, track: CareerTrackResult):
        strong = OpportunityAssessment(level=OpportunityLevel.STRONG, reason="t")
        return compute_pursue_recommendation(
            EligibilityResult(status=EligibilityStatus.ELIGIBLE, checks=[]),
            QualificationResult(status=QualificationStatus.STRONG, reason="t"),
            strong, strong, OpportunityCostResult(level=OpportunityCostLevel.LOW, reason="t"),
            career_track=track,
        )

    def test_aligned_keeps_strong_pursue(self):
        track = CareerTrackResult(track=CareerTrack.CAREER_DIRECTION, alignment=TrackAlignment.ALIGNED, reason="r")
        assert self._pursue(track).recommendation == PursueRecommendation.STRONG_PURSUE

    def test_unclear_caps_at_pursue(self):
        track = CareerTrackResult(track=CareerTrack.NONE, alignment=TrackAlignment.UNCLEAR, reason="r")
        result = self._pursue(track)
        assert result.recommendation == PursueRecommendation.PURSUE
        assert "career_track=unclear" in result.contributing_factors

    def test_off_track_caps_at_consider(self):
        track = CareerTrackResult(track=CareerTrack.NONE, alignment=TrackAlignment.OFF_TRACK, reason="off")
        result = self._pursue(track)
        assert result.recommendation == PursueRecommendation.CONSIDER
        assert "off" in result.reason

    def test_hard_gates_are_never_raised_by_track(self):
        track = CareerTrackResult(track=CareerTrack.CAREER_DIRECTION, alignment=TrackAlignment.ALIGNED, reason="r")
        strong = OpportunityAssessment(level=OpportunityLevel.STRONG, reason="t")
        result = compute_pursue_recommendation(
            EligibilityResult(status=EligibilityStatus.ELIGIBLE, checks=[]),
            QualificationResult(status=QualificationStatus.FAIL, reason="t"),
            strong, strong, OpportunityCostResult(level=OpportunityCostLevel.LOW, reason="t"),
            career_track=track,
        )
        assert result.recommendation == PursueRecommendation.DO_NOT_PURSUE


class TestKeywordMatching:
    def test_short_keywords_no_longer_match_inside_other_words(self):
        job = NormalizedJob(
            source="t", source_job_id="1", source_url="https://e.com/1", title="Office Coordinator",
            description="Maintain email detail in html pages; capital planning. " * 10,
            retrieved_at=NOW,
        )
        assert score_technical_fit(job, CareerProfile.load()).score == 0.0


# --- Through the real pipeline -------------------------------------------

@pytest.fixture
def evidence_index() -> EvidenceIndex:
    role = CareerRole(id="r", company="Acme", title="Senior Engineer", start_date=date(2018, 1, 1))
    prov = EvidenceProvenance(source_type="resume", source_name="fde", company="Acme", career_role_id="r",
                              variant_slug="fde", extracted_at=NOW)

    def ev(id_, type_, skills):
        return Evidence(id=id_, type=type_, statement=id_, canonical_skills=skills, provenance=prov,
                        start_date=role.start_date)

    return EvidenceIndex(evidence=[
        ev("ev-php", SkillCategory.PROGRAMMING_LANGUAGE, ["php", "laravel", "javascript", "typescript"]),
        ev("ev-api", SkillCategory.ARCHITECTURE, ["rest_api", "integration", "platform_engineering", "solutions_architecture"]),
        ev("ev-cloud", SkillCategory.CLOUD, ["aws", "cloud_infrastructure"]),
        ev("ev-ai", SkillCategory.AI_ML, ["ai_ml", "automation", "data_pipeline"]),
    ], career_roles=[role], taxonomy=SkillsTaxonomy.load())


_SHARED_BODY = (
    " You will work with REST APIs and integrations, deploy on AWS cloud infrastructure, use "
    "TypeScript and JavaScript, and apply AI and machine learning features with automation. "
    "Platform engineering and solution architecture experience helps. " * 3
)


def _evaluate(title: str, body: str, evidence_index: EvidenceIndex):
    job = NormalizedJob(
        source="test", source_job_id="1", source_url="https://example.com/1", title=title,
        description=body, employment_type=EmploymentType.FULL_TIME, salary_min=190000, salary_max=230000,
        remote_status=RemoteStatus.REMOTE, location="United States - Remote", retrieved_at=NOW,
    )
    fit = score_job(job, CareerProfile.load(), Preferences.load(), use_ai=False, evidence_index=evidence_index)
    return evaluate_opportunity(job, fit, evidence_index=evidence_index, use_ai=False).decision


class TestPrecisionRegressions:
    @pytest.mark.parametrize("title,body", [
        ("Senior React Native Developer", "Build native mobile applications in React Native for iOS and Android."),
        ("Sr. SAP Commerce Platform Engineer", "Own our SAP Commerce Cloud storefront customizations."),
        ("Senior Technical Program Manager", "Drive program roadmaps and status reporting across teams."),
        ("Forward Deployed Engineering Intern", "Summer internship supporting our deployment team."),
    ])
    def test_unrelated_primary_identity_never_reaches_strong_pursue(self, title, body, evidence_index):
        decision = _evaluate(title, body + _SHARED_BODY, evidence_index)
        assert decision.career_track.alignment == TrackAlignment.OFF_TRACK
        assert decision.pursue.recommendation == PursueRecommendation.CONSIDER

    def test_hands_on_fde_with_matching_substance_stays_strong(self, evidence_index):
        body = (
            "As a Forward Deployed Engineer you write production code, build integrations with REST APIs, "
            "deploy AI applications with LLMs on AWS, and own implementation end to end." + _SHARED_BODY
        )
        decision = _evaluate("Senior Forward Deployed Engineer", body, evidence_index)
        assert decision.career_track.alignment == TrackAlignment.ALIGNED
        assert decision.pursue.recommendation == PursueRecommendation.STRONG_PURSUE

    def test_stored_evaluation_carries_career_track(self, db_session, evidence_index):
        from careers_os.storage.repository import JobRepository
        from careers_os.domain.raw_job import RawJob

        repo = JobRepository(db_session)
        raw = RawJob(source="test", source_job_id="1", source_url="https://e.com/1", raw_title="t",
                     raw_payload={}, retrieved_at=NOW)
        job = NormalizedJob(source="test", source_job_id="1", source_url="https://e.com/1", title="t",
                            retrieved_at=NOW)
        record, _ = repo.upsert_job(job, raw)
        decision = _evaluate("Senior React Native Developer", "Mobile apps." + _SHARED_BODY, evidence_index)
        saved = repo.save_evaluation(record.id, decision)
        assert saved.career_track["alignment"] == "off_track"
