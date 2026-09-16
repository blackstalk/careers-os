"""Behavioral regression tests for three real jobs discovered in Phase 2.5
live validation (see docs/pursue-recommendation.md#regression-cases).

Nothing here special-cases a company or job — these are ordinary inputs
run through the ordinary pipeline (extract_requirements -> evidence
matching -> qualification -> eligibility -> opportunity cost -> pursue).
The synthetic evidence index below captures only the facts relevant to
these cases (PHP/API evidence present; Go/Java/Python absent) so the
tests stay offline and don't depend on the real, personal resume files.
"""

from datetime import date, datetime, timezone

import pytest

from careers_os.career.evidence_index import EvidenceIndex
from careers_os.career.preferences import Preferences
from careers_os.career.profile import CareerProfile
from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.evidence import Evidence, EvidenceProvenance
from careers_os.domain.job import NormalizedJob
from careers_os.domain.opportunity_decision import PursueRecommendation
from careers_os.domain.qualification import QualificationStatus
from careers_os.domain.resume import CareerRole
from careers_os.domain.taxonomy import SkillCategory
from careers_os.ingestion.evaluation import evaluate_opportunity
from careers_os.scoring.engine import score_job

NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)


@pytest.fixture
def evidence_index() -> EvidenceIndex:
    role = CareerRole(id="fifth-ring-2019", company="Fifth Ring", title="Senior Full Stack Developer", start_date=date(2019, 8, 1), end_date=date(2020, 6, 1))
    prov = EvidenceProvenance(source_type="resume", source_name="fde", company="Fifth Ring", career_role_id=role.id, variant_slug="fde", extracted_at=NOW)
    evidence = [
        Evidence(id="ev-php", type=SkillCategory.PROGRAMMING_LANGUAGE, statement="Built PHP/Craft CMS/WordPress platforms.", canonical_skills=["php", "craft_cms", "wordpress"], provenance=prov, start_date=role.start_date, end_date=role.end_date),
        Evidence(id="ev-api", type=SkillCategory.ARCHITECTURE, statement="Built REST API integrations and middleware.", canonical_skills=["rest_api", "integration"], provenance=prov, start_date=role.start_date, end_date=role.end_date),
        Evidence(id="ev-ai", type=SkillCategory.AI_ML, statement="Built AI-assisted automation workflows.", canonical_skills=["ai_ml", "automation"], provenance=prov, start_date=role.start_date, end_date=role.end_date),
        Evidence(id="ev-consult", type=SkillCategory.CUSTOMER_FACING, statement="Advised clients as a technical consultant.", canonical_skills=["technical_consulting", "customer_facing"], provenance=prov, start_date=role.start_date, end_date=role.end_date),
    ]
    return EvidenceIndex(evidence=evidence, career_roles=[role], taxonomy=SkillsTaxonomy.load())


def _job(title: str, description: str, *, employment_type=EmploymentType.CONTRACT, remote_status=RemoteStatus.REMOTE, hourly_min=None, hourly_max=None, posted_at=None) -> NormalizedJob:
    return NormalizedJob(
        source="test", source_job_id="1", source_url="https://example.com/1",
        title=title, description=description, employment_type=employment_type,
        remote_status=remote_status, hourly_min=hourly_min, hourly_max=hourly_max,
        posted_at=posted_at, retrieved_at=NOW,
    )


def _evaluate(job, evidence_index):
    profile = CareerProfile.load()
    preferences = Preferences.load()
    fit = score_job(job, profile, preferences, use_ai=False, evidence_index=evidence_index)
    return evaluate_opportunity(job, fit, evidence_index=evidence_index, use_ai=False)


class TestCaseA_CreativeCirclePHP:
    """Very strong PHP/web alignment, template-driven implementation work,
    little architecture ownership, compensation below the contract floor.
    Must NOT become strong_pursue merely because qualification is excellent.
    """

    def test_is_low_priority_not_strong_pursue(self, evidence_index):
        job = _job(
            "Senior Web Developer - PHP - Fully Remote",
            "We are seeking an experienced Web Developer with PHP experience to support a "
            "planned update and enhancement of our client's website. Execute front-end and "
            "back-end website updates based on approved wireframes and scope. Work within an "
            "existing PHP-based template structure. This role starts at 40 hours per week "
            "through the end of June, transitioning to an ongoing maintenance schedule.",
            hourly_min=70, hourly_max=75, posted_at=NOW,
        )
        result = _evaluate(job, evidence_index)
        assert result.decision.qualification.status == QualificationStatus.STRONG
        assert result.decision.pursue.recommendation not in (
            PursueRecommendation.STRONG_PURSUE, PursueRecommendation.PURSUE,
        )
        assert result.decision.pursue.recommendation == PursueRecommendation.LOW_PRIORITY


class TestCaseB_StripeTechnicalSolutionsEngineer:
    """Strong bridge toward Solutions/FDE work, but a timezone residency
    requirement conflicts with the candidate's configured location. Must
    NOT become strong_pursue while that requirement is unresolved.
    """

    def test_is_verify_first_not_strong_pursue(self, evidence_index):
        job = _job(
            "Technical Solutions Engineer",
            "Communicate with external developers, debug integrations, produce guides, and "
            "build internal support tooling including an LLM-based Copilot. This role is "
            "remote but requires candidates to be located in the Mountain or Pacific time "
            "zones. Minimum requirements: at least 4 years of full-stack software development "
            "experience. Comfortable explaining technical concepts to both technical and "
            "non-technical audiences.",
            posted_at=NOW,
        )
        result = _evaluate(job, evidence_index)
        assert result.decision.pursue.recommendation == PursueRecommendation.VERIFY_FIRST
        assert result.decision.pursue.recommendation != PursueRecommendation.STRONG_PURSUE
        assert any(c.constraint_type.value == "timezone" for c in result.decision.eligibility.checks)


class TestCaseD_HybridAppliedAIEngineer:
    """The real Phase 4.1 production case: OpenAI/Ramp "Applied AI
    Engineer"-style hybrid postings with strong technical/qualification
    signal must NOT become strong_pursue merely because the title/company
    look like the target direction — work arrangement is a hard
    eligibility constraint, checked ahead of any fit score, using the
    real candidate.yaml (work_preferences.hybrid: unacceptable).
    """

    def test_hybrid_role_is_do_not_pursue_regardless_of_strong_fit(self, evidence_index):
        job = _job(
            "Applied AI Engineer, Enterprise",
            "Own solution architecture and AI-assisted automation systems using AWS, REST API "
            "integrations, and provide technical leadership and customer-facing delivery for "
            "enterprise clients.",
            employment_type=EmploymentType.FULL_TIME,
            remote_status=RemoteStatus.HYBRID,
            posted_at=NOW,
        )
        result = _evaluate(job, evidence_index)
        assert result.decision.eligibility.status.value == "ineligible"
        assert result.decision.pursue.recommendation == PursueRecommendation.DO_NOT_PURSUE

    def test_identical_but_remote_role_is_not_blocked_by_eligibility(self, evidence_index):
        # Same content, remote instead of hybrid — proves the block above
        # is specifically about work arrangement, not some other factor
        # coincidentally present in this posting.
        job = _job(
            "Applied AI Engineer, Enterprise",
            "Own solution architecture and AI-assisted automation systems using AWS, REST API "
            "integrations, and provide technical leadership and customer-facing delivery for "
            "enterprise clients.",
            employment_type=EmploymentType.FULL_TIME,
            remote_status=RemoteStatus.REMOTE,
            posted_at=NOW,
        )
        result = _evaluate(job, evidence_index)
        assert result.decision.eligibility.status.value == "eligible"
        assert result.decision.pursue.recommendation != PursueRecommendation.DO_NOT_PURSUE


class TestCaseC_StripeBackendEngineerGo:
    """Discovered via a PHP keyword search, but fundamentally a Go
    engineering position — PHP is only a preferred qualification. Must
    NOT be rescued by preferred PHP experience.
    """

    def test_is_do_not_pursue_not_rescued_by_preferred_php(self, evidence_index):
        job = _job(
            "Backend Engineer, Developer & End-user Experience Platform",
            "Our team owns open-source SDKs and an in-house code generation framework. "
            "Minimum requirements: 6+ years in engineering across a wide range of products. "
            "3+ years of experience as a Golang software engineer. An interest in working "
            "with multiple programming languages. Preferred qualifications: Experience in "
            "PHP and Ruby. Experience building libraries and/or SDKs.",
            posted_at=NOW,
        )
        result = _evaluate(job, evidence_index)
        assert result.decision.qualification.status == QualificationStatus.FAIL
        assert result.decision.pursue.recommendation == PursueRecommendation.DO_NOT_PURSUE
        gap_skills = {m.requirement.canonical_skill for m in result.decision.qualification.hard_gaps}
        assert "go" in gap_skills
        assert "php" not in gap_skills  # PHP is preferred, never a hard gate
