from careers_os.career.bridge_role import BridgeClassification, BridgeRoleResult
from careers_os.career.opportunity_value import OpportunityAssessment, OpportunityLevel
from careers_os.domain.eligibility import (
    ConstraintType,
    EligibilityCheck,
    EligibilityResult,
    EligibilityStatus,
)
from careers_os.domain.experience import ExperienceFitDetail
from careers_os.domain.matching import EvidenceRef, GapType, MatchType, RequirementMatch
from careers_os.domain.opportunity_decision import (
    Freshness,
    FreshnessResult,
    OpportunityCostLevel,
    OpportunityCostResult,
    OpportunityDecision,
    PursueRecommendation,
    PursueResult,
    ScopeResult,
)
from careers_os.domain.qualification import QualificationResult, QualificationStatus
from careers_os.domain.requirements import JobRequirement
from careers_os.domain.scoring import CareerFitResult, FitComponent
from careers_os.domain.taxonomy import SkillCategory
from careers_os.ingestion.evaluation import EvaluationResult
from careers_os.notifications.content import build_alert_content


def _strong_match(skill: str) -> RequirementMatch:
    req = JobRequirement(id=skill, category=SkillCategory.PROGRAMMING_LANGUAGE, canonical_skill=skill, text=skill)
    return RequirementMatch(
        requirement=req, match_type=MatchType.STRONG_MATCH,
        matched_evidence=[EvidenceRef(evidence_id=f"ev-{skill}", statement=f"Did {skill} work.")],
        reason="direct evidence",
    )


def _interview_prep_gap(skill: str) -> RequirementMatch:
    req = JobRequirement(id=skill, category=SkillCategory.CLOUD, canonical_skill=skill, text=skill)
    return RequirementMatch(
        requirement=req, match_type=MatchType.ADJACENT_EXPERIENCE, reason="adjacent evidence only",
        gap_type=GapType.INTERVIEW_PREP_GAP,
    )


def _fit(compensation_score=0.9, compensation_confidence=0.9, detail=None) -> CareerFitResult:
    comp = FitComponent(score=compensation_score, reason="Comp reason.", confidence=compensation_confidence)
    other = FitComponent(score=0.7, reason="r", confidence=0.7)
    return CareerFitResult(
        role_fit=other, technical_fit=other, career_direction_fit=other,
        compensation_fit=comp, work_arrangement_fit=other, experience_fit=other,
        overall_fit=0.8, scorer_version="test", experience_detail=detail,
    )


def _decision(recommendation=PursueRecommendation.STRONG_PURSUE, eligibility_checks=None, opp_cost=OpportunityCostLevel.LOW, freshness=Freshness.FRESH) -> OpportunityDecision:
    return OpportunityDecision(
        eligibility=EligibilityResult(
            status=EligibilityStatus.ELIGIBLE if not eligibility_checks else EligibilityStatus.VERIFY,
            checks=eligibility_checks or [],
        ),
        qualification=QualificationResult(status=QualificationStatus.STRONG, reason="Strong evidence."),
        opportunity_cost=OpportunityCostResult(level=opp_cost, reason="Low cost reason."),
        scope=ScopeResult(reason="test"),
        freshness=FreshnessResult(level=freshness, reason="Stale reason." if freshness == Freshness.STALE else "Fresh."),
        pursue=PursueResult(recommendation=recommendation, reason="Strong across the board."),
    )


def _result(**kwargs) -> EvaluationResult:
    detail = ExperienceFitDetail(requirement_matches=[_strong_match("aws"), _strong_match("rest_api")])
    fit = _fit(detail=detail)
    decision = _decision(**kwargs)
    bridge = BridgeRoleResult(classification=BridgeClassification.STRONG, reason="Strong bridge reason.", signals=["AWS"])
    direction = OpportunityAssessment(level=OpportunityLevel.STRONG, reason="Direction reason.")
    immediate = OpportunityAssessment(level=OpportunityLevel.STRONG, reason="Immediate reason.")
    return EvaluationResult(decision=decision, fit=fit, bridge=bridge, immediate=immediate, direction=direction)


class TestAlertContentReasons:
    def test_reasons_are_derived_from_real_evidence(self):
        result = _result()
        content = build_alert_content(
            "Acme", "Solutions Architect", "Remote", "remote", "$150/hr",
            "greenhouse", "https://example.com/job", result,
        )
        assert any("bridge" in r.lower() for r in content.reasons)
        assert any("aws" in r.lower() or "rest_api" in r.lower() for r in content.reasons)
        assert len(content.reasons) <= 5

    def test_reasons_never_empty(self):
        # Even a bare-minimum evaluation gets a fallback reason, never an
        # empty list (which would produce a useless email body).
        detail = ExperienceFitDetail(requirement_matches=[])
        fit = _fit(compensation_score=0.5, compensation_confidence=0.1, detail=detail)
        decision = OpportunityDecision(
            eligibility=EligibilityResult(status=EligibilityStatus.ELIGIBLE),
            qualification=QualificationResult(status=QualificationStatus.UNKNOWN, reason="r"),
            opportunity_cost=OpportunityCostResult(level=OpportunityCostLevel.UNKNOWN, reason="r"),
            scope=ScopeResult(reason="r"),
            freshness=FreshnessResult(level=Freshness.UNKNOWN, reason="r"),
            pursue=PursueResult(recommendation=PursueRecommendation.STRONG_PURSUE, reason="r"),
        )
        bridge = BridgeRoleResult(classification=BridgeClassification.NONE, reason="r")
        direction = OpportunityAssessment(level=OpportunityLevel.WEAK, reason="r")
        immediate = OpportunityAssessment(level=OpportunityLevel.WEAK, reason="r")
        result = EvaluationResult(decision=decision, fit=fit, bridge=bridge, immediate=immediate, direction=direction)
        content = build_alert_content("Acme", "Role", "-", "-", "-", "s", "u", result)
        assert content.reasons


class TestAlertContentWatchouts:
    def test_verify_eligibility_check_becomes_a_watchout(self):
        check = EligibilityCheck(
            requirement="located in Pacific time zone", constraint_type=ConstraintType.TIMEZONE,
            candidate_evidence="America/Chicago", status=EligibilityStatus.VERIFY, confidence=0.5,
            reason="Ambiguous timezone wording.",
        )
        result = _result(eligibility_checks=[check])
        content = build_alert_content("Acme", "Role", "-", "-", "-", "s", "u", result)
        assert any("timezone" in w.lower() or "pacific" in w.lower() for w in content.watchouts)

    def test_unpublished_compensation_is_a_watchout(self):
        result = _result()
        result.fit = _fit(compensation_score=0.5, compensation_confidence=0.05, detail=result.fit.experience_detail)
        content = build_alert_content("Acme", "Role", "-", "-", "-", "s", "u", result)
        assert any("compensation" in w.lower() for w in content.watchouts)

    def test_interview_prep_gap_becomes_a_watchout(self):
        detail = ExperienceFitDetail(requirement_matches=[_strong_match("aws"), _interview_prep_gap("kubernetes")])
        result = _result()
        result.fit = _fit(detail=detail)
        content = build_alert_content("Acme", "Role", "-", "-", "-", "s", "u", result)
        assert any("kubernetes" in w.lower() for w in content.watchouts)

    def test_stale_posting_is_a_watchout(self):
        result = _result(freshness=Freshness.STALE)
        content = build_alert_content("Acme", "Role", "-", "-", "-", "s", "u", result)
        assert any("stale" in w.lower() for w in content.watchouts)


class TestAlertContentFormatting:
    def test_subject_includes_company_and_role(self):
        result = _result()
        content = build_alert_content("Acme", "Solutions Architect", "-", "-", "-", "s", "u", result)
        assert "Acme" in content.subject
        assert "Solutions Architect" in content.subject

    def test_plain_text_includes_url_and_pursue(self):
        result = _result()
        content = build_alert_content("Acme", "Role", "-", "-", "-", "s", "https://x.com/1", result)
        text = content.to_plain_text()
        assert "https://x.com/1" in text
        assert "STRONG PURSUE" in text or "strong_pursue" in text.lower()
