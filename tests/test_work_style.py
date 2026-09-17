"""Phase 4.2 — work-style fit / role-shape intelligence.

Classifier behavior on real-shaped fixtures (tests/fixtures/work_style),
its interaction with pursue-worthiness, persistence, and alert content.
See docs/pursue-recommendation.md#work-style-fit.
"""

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from careers_os.career.evidence_index import EvidenceIndex
from careers_os.career.opportunity_value import OpportunityAssessment, OpportunityLevel
from careers_os.career.preferences import Preferences
from careers_os.career.profile import CareerProfile
from careers_os.career.pursue import compute_pursue_recommendation
from careers_os.career.skills import SkillsTaxonomy
from careers_os.career.work_style import classify_work_style
from careers_os.domain.eligibility import EligibilityResult, EligibilityStatus
from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.evidence import Evidence, EvidenceProvenance
from careers_os.domain.job import NormalizedJob
from careers_os.domain.opportunity_decision import (
    OpportunityCostLevel,
    OpportunityCostResult,
    PursueRecommendation,
    WorkStyle,
    WorkStyleResult,
)
from careers_os.domain.qualification import QualificationResult, QualificationStatus
from careers_os.domain.raw_job import RawJob
from careers_os.domain.resume import CareerRole
from careers_os.domain.taxonomy import SkillCategory
from careers_os.ingestion.evaluation import evaluate_opportunity
from careers_os.notifications.content import build_alert_content
from careers_os.scoring.engine import score_job
from careers_os.storage.db import get_engine
from careers_os.storage.repository import JobRepository

FIXTURES = Path(__file__).parent / "fixtures" / "work_style"
NOW = datetime.now(timezone.utc)


def _text(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text()


class TestClassifierOnFixtures:
    def test_ramp_technical_consultant_is_customer_heavy(self):
        result = classify_work_style(_text("ramp_technical_consultant"))
        assert result.style == WorkStyle.CUSTOMER_HEAVY
        for phrase in ("on the frontlines", "liaison", "account management", "externally", "success criteria"):
            assert phrase in result.customer_signals
        assert result.no_code_signals == ["without writing code directly"]
        # Counter-evidence stays visible rather than being hidden.
        assert "troubleshoot" in result.build_signals
        assert "without writing code directly" in result.reason

    def test_workos_applied_ai_engineer_is_build_heavy(self):
        result = classify_work_style(_text("workos_applied_ai_engineer"))
        assert result.style == WorkStyle.BUILD_HEAVY
        assert {"design and ship", "from idea to production", "owning services", "observab"} <= set(result.build_signals)
        assert result.customer_signals == []

    def test_hands_on_fde_is_not_penalized_for_customer_contact(self):
        result = classify_work_style(_text("synthetic_fde"))
        assert result.style in (WorkStyle.BUILD_HEAVY, WorkStyle.BALANCED)
        assert result.customer_signals  # customer contact is detected...
        assert {"production code", "deploy", "debug"} <= set(result.build_signals)  # ...but building dominates

    def test_presales_solutions_architect_is_customer_heavy(self):
        result = classify_work_style(_text("synthetic_presales_sa"))
        assert result.style == WorkStyle.CUSTOMER_HEAVY
        assert result.build_signals == []
        assert {"demos", "pre-sales", "discovery calls", "executive relationships"} <= set(result.customer_signals)
        assert {"coordinate", "presentations"} <= set(result.coordination_signals)


class TestClassifierRules:
    def test_company_about_and_benefits_boilerplate_are_ignored(self):
        # "everyone is a builder" / "build systems" sit in the About section
        # of the Ramp fixture and must not count as build evidence.
        result = classify_work_style(_text("ramp_technical_consultant"))
        assert "build systems" not in " ".join(result.build_signals)
        assert "travel" not in " ".join(result.customer_signals)

    def test_customer_facing_product_is_not_a_customer_signal(self):
        # WorkOS ships "customer-facing AI products" and builds a tool that
        # does "meeting prep, and follow-up" — neither is the engineer's work.
        result = classify_work_style(_text("workos_applied_ai_engineer"))
        assert result.customer_signals == []
        assert result.coordination_signals == []

    def test_product_level_no_code_wording_does_not_cap_building(self):
        description = (
            "About the role\nYou will design and build the platform that lets researchers stand up a new "
            "data collection effort without writing code. You'll build services, deploy them, debug "
            "production systems, and own the implementation of our backend services. " * 2
        )
        result = classify_work_style(description)
        assert result.no_code_signals == []
        assert result.style == WorkStyle.BUILD_HEAVY

    def test_generic_collaboration_language_alone_does_not_make_a_role_meeting_heavy(self):
        description = (
            "About the role\nStaff engineer on our ML platform. You will prototype new training "
            "infrastructure and work cross-functional with research to influence the roadmap. "
            "Hands-on, senior individual contributor role with a lot of autonomy. " * 2
        )
        result = classify_work_style(description)
        assert result.style not in (WorkStyle.CUSTOMER_HEAVY, WorkStyle.COORDINATION_HEAVY)

    def test_short_or_missing_description_is_unknown(self):
        assert classify_work_style(None).style == WorkStyle.UNKNOWN
        assert classify_work_style("Solutions Architect. Remote.").style == WorkStyle.UNKNOWN

    def test_classification_ignores_title_entirely(self):
        # The classifier takes only the description; the same text gets the
        # same answer whatever the title would have been.
        assert classify_work_style.__code__.co_argcount == 1


def _eligible() -> EligibilityResult:
    return EligibilityResult(status=EligibilityStatus.ELIGIBLE)


def _strong(level=OpportunityLevel.STRONG) -> OpportunityAssessment:
    return OpportunityAssessment(level=level, reason="test")


def _ws(style: WorkStyle) -> WorkStyleResult:
    return WorkStyleResult(style=style, reason="test")


DISFAVORED = {WorkStyle.CUSTOMER_HEAVY, WorkStyle.COORDINATION_HEAVY}


class TestPursueInteraction:
    def _pursue(self, work_style, disfavored=DISFAVORED, eligibility=None, qualification=QualificationStatus.STRONG, cost=OpportunityCostLevel.LOW):
        return compute_pursue_recommendation(
            eligibility or _eligible(),
            QualificationResult(status=qualification, reason="test"),
            _strong(), _strong(),
            OpportunityCostResult(level=cost, reason="cost reason"),
            work_style=work_style,
            disfavored_work_styles=disfavored,
        )

    @pytest.mark.parametrize("style", [WorkStyle.CUSTOMER_HEAVY, WorkStyle.COORDINATION_HEAVY])
    def test_disfavored_style_caps_an_otherwise_strong_role_at_consider(self, style):
        result = self._pursue(_ws(style))
        assert result.recommendation == PursueRecommendation.CONSIDER
        assert style.value.replace("_", " ") in result.reason
        assert f"work_style={style.value}" in result.contributing_factors

    @pytest.mark.parametrize("style", [WorkStyle.BUILD_HEAVY, WorkStyle.BALANCED, WorkStyle.UNKNOWN])
    def test_other_styles_do_not_change_a_strong_recommendation(self, style):
        assert self._pursue(_ws(style)).recommendation == PursueRecommendation.STRONG_PURSUE

    def test_no_disfavored_preference_means_no_cap(self):
        result = self._pursue(_ws(WorkStyle.CUSTOMER_HEAVY), disfavored=set())
        assert result.recommendation == PursueRecommendation.STRONG_PURSUE

    def test_hard_gates_still_take_precedence(self):
        from careers_os.domain.eligibility import ConstraintType, EligibilityCheck

        ineligible = EligibilityResult(
            status=EligibilityStatus.INELIGIBLE,
            checks=[EligibilityCheck(
                requirement="Hybrid presence required", constraint_type=ConstraintType.WORK_ARRANGEMENT,
                candidate_evidence="x", status=EligibilityStatus.INELIGIBLE, confidence=0.9, reason="x",
            )],
        )
        assert self._pursue(_ws(WorkStyle.CUSTOMER_HEAVY), eligibility=ineligible).recommendation == PursueRecommendation.DO_NOT_PURSUE
        assert self._pursue(_ws(WorkStyle.CUSTOMER_HEAVY), qualification=QualificationStatus.FAIL).recommendation == PursueRecommendation.DO_NOT_PURSUE

    def test_high_opportunity_cost_still_wins_over_work_style(self):
        result = self._pursue(_ws(WorkStyle.CUSTOMER_HEAVY), cost=OpportunityCostLevel.HIGH)
        assert result.recommendation == PursueRecommendation.LOW_PRIORITY

    def test_work_style_is_optional_for_existing_callers(self):
        result = compute_pursue_recommendation(
            _eligible(), QualificationResult(status=QualificationStatus.STRONG, reason="t"),
            _strong(), _strong(), OpportunityCostResult(level=OpportunityCostLevel.LOW, reason="t"),
        )
        assert result.recommendation == PursueRecommendation.STRONG_PURSUE


@pytest.fixture
def evidence_index() -> EvidenceIndex:
    role = CareerRole(id="r1", company="Acme", title="Senior Engineer", start_date=date(2016, 1, 1))
    prov = EvidenceProvenance(
        source_type="resume", source_name="fde", company="Acme", career_role_id=role.id,
        variant_slug="fde", extracted_at=NOW,
    )

    def ev(id_, type_, statement, skills):
        return Evidence(id=id_, type=type_, statement=statement, canonical_skills=skills,
                        provenance=prov, start_date=role.start_date, end_date=role.end_date)

    return EvidenceIndex(
        evidence=[
            ev("ev-api", SkillCategory.ARCHITECTURE, "Designed REST API integrations and solution architecture.",
               ["rest_api", "integration", "solutions_architecture", "system_design"]),
            ev("ev-aws", SkillCategory.CLOUD, "Ran production AWS infrastructure.", ["aws", "cloud_infrastructure"]),
            ev("ev-ai", SkillCategory.AI_ML, "Shipped LLM-backed features and RAG search.", ["ai_ml", "automation"]),
            ev("ev-lang", SkillCategory.PROGRAMMING_LANGUAGE, "PHP, TypeScript, and Python services.", ["php", "javascript", "typescript", "python"]),
            ev("ev-cust", SkillCategory.CUSTOMER_FACING, "Led customer-facing technical delivery.",
               ["customer_facing", "technical_consulting", "stakeholder_communication"]),
        ],
        career_roles=[role],
        taxonomy=SkillsTaxonomy.load(),
    )


def _job(title: str, fixture: str, source: str = "test") -> NormalizedJob:
    return NormalizedJob(
        source=source, source_job_id=fixture, source_url=f"https://example.com/{fixture}",
        title=title, company="ExampleCo", description=_text(fixture),
        employment_type=EmploymentType.FULL_TIME, remote_status=RemoteStatus.REMOTE,
        salary_min=190000, salary_max=260000, posted_at=NOW, retrieved_at=NOW,
    )


def _evaluate(job, evidence_index):
    fit = score_job(job, CareerProfile.load(), Preferences.load(), use_ai=False, evidence_index=evidence_index)
    return evaluate_opportunity(job, fit, evidence_index=evidence_index, use_ai=False)


class TestEndToEnd:
    def test_ramp_consultant_is_not_a_top_recommendation_despite_technical_match(self, evidence_index):
        result = _evaluate(_job("Technical Consultant, Commercial", "ramp_technical_consultant"), evidence_index)
        assert result.decision.eligibility.status == EligibilityStatus.ELIGIBLE
        assert result.decision.work_style.style == WorkStyle.CUSTOMER_HEAVY
        assert result.decision.pursue.recommendation not in (PursueRecommendation.STRONG_PURSUE, PursueRecommendation.PURSUE)
        assert "customer heavy" in result.decision.pursue.reason

    def test_workos_applied_ai_engineer_stays_aligned(self, evidence_index):
        result = _evaluate(_job("Applied AI Engineer", "workos_applied_ai_engineer"), evidence_index)
        assert result.decision.work_style.style == WorkStyle.BUILD_HEAVY
        assert "work style" not in result.decision.pursue.reason.lower()
        assert result.decision.pursue.recommendation in (PursueRecommendation.STRONG_PURSUE, PursueRecommendation.PURSUE)

    def test_hands_on_fde_is_not_downgraded_for_customer_contact(self, evidence_index):
        result = _evaluate(_job("Forward Deployed Engineer", "synthetic_fde"), evidence_index)
        assert result.decision.work_style.style in (WorkStyle.BUILD_HEAVY, WorkStyle.BALANCED)
        assert "day-to-day shape" not in result.decision.pursue.reason

    def test_presales_sa_is_downgraded_despite_solutions_architect_title(self, evidence_index):
        result = _evaluate(_job("Solutions Architect", "synthetic_presales_sa"), evidence_index)
        assert result.decision.work_style.style == WorkStyle.CUSTOMER_HEAVY
        assert result.decision.pursue.recommendation not in (PursueRecommendation.STRONG_PURSUE, PursueRecommendation.PURSUE)

    def test_same_description_gets_the_same_work_style_under_any_title_or_source(self, evidence_index):
        styles = {
            _evaluate(_job(title, "synthetic_presales_sa", source=source), evidence_index).decision.work_style.style
            for title, source in [("Solutions Architect", "greenhouse"), ("Forward Deployed Engineer", "ashby"),
                                  ("Applied AI Engineer", "lever"), ("Platform Engineer", "workable")]
        }
        assert styles == {WorkStyle.CUSTOMER_HEAVY}


class TestPersistenceAndAlerts:
    def test_evaluation_record_stores_work_style(self, db_session, evidence_index):
        repo = JobRepository(db_session)
        job = _job("Solutions Architect", "synthetic_presales_sa")
        raw = RawJob(source=job.source, source_job_id=job.source_job_id, source_url=job.source_url,
                     raw_title=job.title, raw_payload={}, retrieved_at=NOW)
        record, _ = repo.upsert_job(job, raw)
        decision = _evaluate(job, evidence_index).decision
        saved = repo.save_evaluation(record.id, decision)
        assert saved.work_style["style"] == "customer_heavy"
        assert "demos" in saved.work_style["customer_signals"]
        assert saved.evaluation_version == "opportunity-decision-v3"

    def test_existing_database_gets_work_style_column(self, tmp_path):
        path = tmp_path / "old.db"
        engine = create_engine(f"sqlite:///{path}")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE job_evaluations (id INTEGER PRIMARY KEY, job_id INTEGER)"))
        engine.dispose()
        migrated = get_engine(path)
        assert "work_style" in {c["name"] for c in inspect(migrated).get_columns("job_evaluations")}

    def test_alert_shows_work_style_and_flags_customer_time_for_balanced_roles(self, evidence_index):
        result = _evaluate(_job("Forward Deployed Engineer", "synthetic_fde"), evidence_index)
        balanced = result.decision.model_copy(update={"work_style": WorkStyleResult(
            style=WorkStyle.BALANCED, reason="t", customer_signals=["meet with customers", "technical discovery"],
        )})
        result.decision = balanced
        content = build_alert_content("ExampleCo", "FDE", "Remote", "remote", "-", "test", "u", result)
        text_body = content.to_plain_text()
        assert "Work style: balanced" in text_body
        assert any("meet with customers" in w for w in content.watchouts)
