"""Phase 4.1 production-relevance regressions: remote compatibility,
two-path career transitions, provider neutrality, and the production
workflow invocation. Uses the real candidate.yaml / profile.yaml /
preferences.yaml, with a synthetic evidence index standing in for the
personal resume.
"""

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml

from careers_os.career.evidence_index import EvidenceIndex
from careers_os.career.preferences import Preferences
from careers_os.career.profile import CareerProfile
from careers_os.career.search_profiles import SearchProfilesConfig
from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.eligibility import EligibilityStatus
from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.evidence import Evidence, EvidenceProvenance
from careers_os.domain.job import NormalizedJob
from careers_os.domain.opportunity_decision import PursueRecommendation
from careers_os.domain.resume import CareerRole
from careers_os.domain.taxonomy import SkillCategory
from careers_os.ingestion.evaluation import evaluate_opportunity
from careers_os.scoring.engine import score_job

NOW = datetime.now(timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def evidence_index() -> EvidenceIndex:
    role = CareerRole(id="r1", company="Acme", title="Senior Engineer", start_date=date(2016, 1, 1))
    prov = EvidenceProvenance(
        source_type="resume", source_name="fde", company="Acme", career_role_id=role.id,
        variant_slug="fde", extracted_at=NOW,
    )

    def ev(id_, type_, statement, skills):
        return Evidence(
            id=id_, type=type_, statement=statement, canonical_skills=skills,
            provenance=prov, start_date=role.start_date, end_date=role.end_date,
        )

    return EvidenceIndex(
        evidence=[
            ev("ev-php", SkillCategory.PROGRAMMING_LANGUAGE, "Built PHP/Laravel/Craft platforms.", ["php", "laravel", "craft_cms"]),
            ev("ev-api", SkillCategory.ARCHITECTURE, "Designed REST API integrations and solution architecture.", ["rest_api", "integration", "solutions_architecture"]),
            ev("ev-aws", SkillCategory.CLOUD, "Ran production AWS infrastructure.", ["aws", "cloud_infrastructure"]),
            ev("ev-ai", SkillCategory.AI_ML, "Shipped LLM-backed application features.", ["ai_ml", "automation"]),
            ev("ev-cust", SkillCategory.CUSTOMER_FACING, "Led customer-facing technical delivery.", ["customer_facing", "technical_consulting"]),
        ],
        career_roles=[role],
        taxonomy=SkillsTaxonomy.load(),
    )


def _job(title: str, description: str, *, source: str = "test", remote_status=RemoteStatus.REMOTE, company="ExampleCo") -> NormalizedJob:
    return NormalizedJob(
        source=source, source_job_id="1", source_url="https://example.com/1",
        title=title, company=company, description=description,
        employment_type=EmploymentType.FULL_TIME, remote_status=remote_status,
        salary_min=190000, salary_max=230000, posted_at=NOW, retrieved_at=NOW,
    )


def _evaluate(job, evidence_index):
    fit = score_job(job, CareerProfile.load(), Preferences.load(), use_ai=False, evidence_index=evidence_index)
    return evaluate_opportunity(job, fit, evidence_index=evidence_index, use_ai=False)


_AI_ROLE_DESCRIPTION = (
    "Design solution architecture for enterprise customers, build REST API integrations on AWS, "
    "and ship LLM-backed AI features in customer-facing engagements."
)
_UNRELATED_AI_DESCRIPTION = (
    "Minimum requirements: 5+ years of experience with CUDA kernel development and 5+ years of "
    "experience training large-scale models in PyTorch. Forward deployed applied AI engineer."
)


class TestTitleAloneCannotCarryAnOpportunity:
    def test_fde_title_with_unsupported_hard_requirements_is_not_pursued(self, evidence_index):
        job = _job("Forward Deployed Engineer", _UNRELATED_AI_DESCRIPTION)
        result = _evaluate(job, evidence_index)
        assert result.decision.pursue.recommendation not in (
            PursueRecommendation.STRONG_PURSUE, PursueRecommendation.PURSUE,
        )

    def test_applied_ai_title_with_unsupported_hard_requirements_is_not_pursued(self, evidence_index):
        job = _job("Applied AI Engineer", _UNRELATED_AI_DESCRIPTION)
        result = _evaluate(job, evidence_index)
        assert result.decision.pursue.recommendation not in (
            PursueRecommendation.STRONG_PURSUE, PursueRecommendation.PURSUE,
        )


class TestSpecializedPlatformRequirements:
    def test_erp_specific_consultant_role_is_not_strong_pursue(self, evidence_index):
        # Real Ramp "Technical Consultant, S/4HANA" shape: generic API /
        # integration wording plus a hard ERP-specific requirement the
        # candidate has no direct evidence for.
        job = _job(
            "Technical Consultant, S/4HANA",
            "Act as a technical advisor designing solution architecture and REST API integrations "
            "for customer-facing implementations on AWS.\n\nWHAT YOU NEED\n"
            " - 3+ years of hands-on experience working with SAP S/4HANA, including finance modules",
        )
        result = _evaluate(job, evidence_index)
        assert result.decision.pursue.recommendation not in (
            PursueRecommendation.STRONG_PURSUE, PursueRecommendation.PURSUE,
        )
        assert any(m.requirement.canonical_skill == "erp_systems" for m in result.decision.qualification.hard_gaps)


class TestTwoTransitionPaths:
    def test_target_direction_role_viable_without_php_keywords(self, evidence_index):
        # Path B: no PHP/Laravel/Craft/WordPress anywhere, but real
        # transferable evidence overlap.
        job = _job("Applied AI Engineer", _AI_ROLE_DESCRIPTION)
        assert "php" not in job.description.lower() and "laravel" not in job.description.lower()
        result = _evaluate(job, evidence_index)
        assert result.decision.eligibility.status == EligibilityStatus.ELIGIBLE
        assert result.decision.pursue.recommendation in (
            PursueRecommendation.STRONG_PURSUE, PursueRecommendation.PURSUE,
        )
        assert "evidence" in result.fit.career_direction_fit.reason.lower()

    def test_stack_adjacent_role_viable_without_fde_title(self, evidence_index):
        # Path A: a remote Laravel/AWS architecture role with no FDE/AI title.
        job = _job(
            "Senior Laravel Platform Engineer",
            "Own the architecture of our Laravel/PHP platform, design REST API integrations, "
            "and run production infrastructure on AWS. Lead technical design with customers.",
        )
        result = _evaluate(job, evidence_index)
        assert result.decision.eligibility.status == EligibilityStatus.ELIGIBLE
        assert result.decision.pursue.recommendation in (
            PursueRecommendation.STRONG_PURSUE, PursueRecommendation.PURSUE,
        )

    def test_stack_adjacent_and_target_direction_profiles_are_both_enabled(self):
        enabled = {p.name for p in SearchProfilesConfig.load().enabled_profiles()}
        assert {"php", "laravel", "craft", "wordpress", "backend_platform", "fde"} <= enabled


class TestRemoteCompatibilityEndToEnd:
    @pytest.mark.parametrize("status", [RemoteStatus.HYBRID, RemoteStatus.ONSITE])
    def test_non_remote_strong_role_is_do_not_pursue(self, evidence_index, status):
        result = _evaluate(_job("Applied AI Engineer", _AI_ROLE_DESCRIPTION, remote_status=status), evidence_index)
        assert result.decision.pursue.recommendation == PursueRecommendation.DO_NOT_PURSUE

    def test_country_scoped_remote_role_requires_verification(self, evidence_index):
        job = _job("Applied AI Engineer", _AI_ROLE_DESCRIPTION).model_copy(update={"location": "India - Remote"})
        result = _evaluate(job, evidence_index)
        assert result.decision.pursue.recommendation == PursueRecommendation.VERIFY_FIRST


class TestProviderNeutrality:
    def test_identical_job_gets_identical_decision_across_sources_and_companies(self, evidence_index):
        outcomes = set()
        for source, company in [
            ("greenhouse", "OpenAI"), ("ashby", "Ramp"), ("lever", "Palantir"),
            ("workable", "Small Unknown Co"), ("creative_circle", None),
        ]:
            result = _evaluate(_job("Applied AI Engineer", _AI_ROLE_DESCRIPTION, source=source, company=company), evidence_index)
            outcomes.add((
                result.decision.pursue.recommendation,
                result.decision.qualification.status,
                result.fit.overall_fit,
                result.fit.career_direction_fit.score,
            ))
        assert len(outcomes) == 1


class TestProductionWorkflowInvocation:
    def test_workflow_invokes_cli_as_a_module(self):
        workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/scheduled-run.yml").read_text())
        run_commands = [s.get("run", "") for s in workflow["jobs"]["run"]["steps"]]
        assert "python -m careers_os.cli run" in run_commands
        # bash's `jobs` builtin swallows a bare `jobs run` — must never come back.
        assert not any(cmd.strip().startswith("jobs ") for cmd in run_commands)

    def test_workflow_persists_via_cache_with_seed_fallback(self):
        text = (REPO_ROOT / ".github/workflows/scheduled-run.yml").read_text()
        assert "actions/cache/restore" in text and "actions/cache/save" in text
        assert "careers.seed.db" in text
        assert "git push" not in text

    def test_cli_module_is_executable(self):
        source = (REPO_ROOT / "src/careers_os/cli.py").read_text()
        assert 'if __name__ == "__main__":' in source

    def test_seed_database_is_committed(self):
        assert (REPO_ROOT / "data/careers.seed.db").exists()
