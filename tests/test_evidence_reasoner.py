from datetime import datetime, timezone

from careers_os.career.evidence_index import EvidenceIndex
from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.evidence import Evidence, EvidenceProvenance
from careers_os.domain.experience import ExperienceFitDetail
from careers_os.domain.matching import MatchType, RequirementMatch
from careers_os.domain.requirements import JobRequirement, RequirementImportance
from careers_os.domain.taxonomy import SkillCategory
from careers_os.scoring.evidence_reasoner import EvidenceReasoner, apply_evidence_reasoning


def _evidence(evidence_id: str, statement: str) -> Evidence:
    return Evidence(
        id=evidence_id,
        type=SkillCategory.ARCHITECTURE,
        statement=statement,
        canonical_skills=["rest_api", "integration"],
        provenance=EvidenceProvenance(
            source_type="resume", source_name="fde", variant_slug="fde",
            extracted_at=datetime.now(timezone.utc),
        ),
    )


def _req(req_id: str, importance: RequirementImportance) -> JobRequirement:
    return JobRequirement(id=req_id, category=SkillCategory.PROGRAMMING_LANGUAGE, canonical_skill=req_id, text=req_id, importance=importance)


def _match(req_id: str, match_type: MatchType, importance: RequirementImportance = RequirementImportance.REQUIRED) -> RequirementMatch:
    return RequirementMatch(requirement=_req(req_id, importance), match_type=match_type, reason="deterministic")


class FakeReasoner(EvidenceReasoner):
    def __init__(self, raw_output: list[dict]) -> None:
        self.raw_output = raw_output
        self.received_gaps: list[RequirementMatch] = []

    def reason_about_gaps(self, job_title, job_description, gaps, evidence_index):
        self.received_gaps = gaps
        return self.raw_output


def _index() -> EvidenceIndex:
    return EvidenceIndex(
        evidence=[_evidence("ev-1", "Extensive REST API integration work.")],
        career_roles=[],
        taxonomy=SkillsTaxonomy.load(),
    )


class TestValidAssessmentIsAttached:
    def test_transferable_capability_with_valid_citation_is_attached(self):
        detail = ExperienceFitDetail(requirement_matches=[_match("java", MatchType.UNSUPPORTED)])
        reasoner = FakeReasoner([{
            "requirement_id": "java",
            "assessment_type": "transferable_capability",
            "evidence_ids": ["ev-1"],
            "reason": "Multi-language API integration experience transfers.",
            "confidence": "moderate",
        }])
        updated = apply_evidence_reasoning("Job", "desc", detail, _index(), reasoner)
        match = updated.requirement_matches[0]
        assert match.ai_assessment is not None
        assert match.ai_assessment.assessment_type.value == "transferable_capability"
        # Deterministic fields must remain untouched.
        assert match.match_type == MatchType.UNSUPPORTED


class TestGuardrails:
    def test_unknown_evidence_id_is_rejected(self):
        detail = ExperienceFitDetail(requirement_matches=[_match("java", MatchType.UNSUPPORTED)])
        reasoner = FakeReasoner([{
            "requirement_id": "java",
            "assessment_type": "transferable_capability",
            "evidence_ids": ["ev-does-not-exist"],
            "reason": "made up",
            "confidence": "high",
        }])
        updated = apply_evidence_reasoning("Job", "desc", detail, _index(), reasoner)
        assert updated.requirement_matches[0].ai_assessment is None

    def test_unknown_requirement_id_is_rejected(self):
        detail = ExperienceFitDetail(requirement_matches=[_match("java", MatchType.UNSUPPORTED)])
        reasoner = FakeReasoner([{
            "requirement_id": "python",  # not one of the gaps offered
            "assessment_type": "transferable_capability",
            "evidence_ids": ["ev-1"],
            "reason": "made up",
            "confidence": "high",
        }])
        updated = apply_evidence_reasoning("Job", "desc", detail, _index(), reasoner)
        assert updated.requirement_matches[0].ai_assessment is None

    def test_empty_evidence_ids_is_rejected(self):
        detail = ExperienceFitDetail(requirement_matches=[_match("java", MatchType.UNSUPPORTED)])
        reasoner = FakeReasoner([{
            "requirement_id": "java",
            "assessment_type": "transferable_capability",
            "evidence_ids": [],
            "reason": "no evidence cited but claiming transfer anyway",
            "confidence": "high",
        }])
        updated = apply_evidence_reasoning("Job", "desc", detail, _index(), reasoner)
        assert updated.requirement_matches[0].ai_assessment is None

    def test_ai_cannot_claim_direct_or_strong_evidence(self):
        # The schema itself has no "direct_evidence"/"strong_match" value —
        # any attempt to assert one fails validation and is rejected.
        detail = ExperienceFitDetail(requirement_matches=[_match("java", MatchType.UNSUPPORTED)])
        reasoner = FakeReasoner([{
            "requirement_id": "java",
            "assessment_type": "direct_evidence",  # not a real AIAssessmentType value
            "evidence_ids": ["ev-1"],
            "reason": "trying to claim direct experience",
            "confidence": "high",
        }])
        updated = apply_evidence_reasoning("Job", "desc", detail, _index(), reasoner)
        assert updated.requirement_matches[0].ai_assessment is None

    def test_ai_cannot_invent_years_of_experience(self):
        # No "years" field exists in the schema — extra fields are forbidden.
        detail = ExperienceFitDetail(requirement_matches=[_match("java", MatchType.UNSUPPORTED)])
        reasoner = FakeReasoner([{
            "requirement_id": "java",
            "assessment_type": "transferable_capability",
            "evidence_ids": ["ev-1"],
            "reason": "claiming years",
            "confidence": "high",
            "years_of_experience": 5,  # invented field
        }])
        updated = apply_evidence_reasoning("Job", "desc", detail, _index(), reasoner)
        assert updated.requirement_matches[0].ai_assessment is None

    def test_malformed_confidence_value_is_rejected(self):
        detail = ExperienceFitDetail(requirement_matches=[_match("java", MatchType.UNSUPPORTED)])
        reasoner = FakeReasoner([{
            "requirement_id": "java",
            "assessment_type": "transferable_capability",
            "evidence_ids": ["ev-1"],
            "reason": "bad confidence",
            "confidence": "extremely certain",  # not low/moderate/high
        }])
        updated = apply_evidence_reasoning("Job", "desc", detail, _index(), reasoner)
        assert updated.requirement_matches[0].ai_assessment is None


class TestCostControl:
    def test_hard_required_gaps_are_never_sent_to_the_reasoner(self):
        detail = ExperienceFitDetail(requirement_matches=[
            _match("go", MatchType.UNSUPPORTED, RequirementImportance.HARD_REQUIRED),
        ])
        reasoner = FakeReasoner([])
        apply_evidence_reasoning("Job", "desc", detail, _index(), reasoner)
        assert reasoner.received_gaps == []

    def test_strong_matches_are_never_sent_to_the_reasoner(self):
        detail = ExperienceFitDetail(requirement_matches=[_match("php", MatchType.STRONG_MATCH)])
        reasoner = FakeReasoner([])
        apply_evidence_reasoning("Job", "desc", detail, _index(), reasoner)
        assert reasoner.received_gaps == []

    def test_only_unsupported_and_adjacent_non_hard_gaps_are_sent(self):
        unsupported = _match("java", MatchType.UNSUPPORTED)
        adjacent = _match("python", MatchType.ADJACENT_EXPERIENCE)
        detail = ExperienceFitDetail(requirement_matches=[
            unsupported, adjacent,
            _match("php", MatchType.STRONG_MATCH),
            _match("go", MatchType.UNSUPPORTED, RequirementImportance.HARD_REQUIRED),
        ])
        reasoner = FakeReasoner([])
        apply_evidence_reasoning("Job", "desc", detail, _index(), reasoner)
        sent_ids = {m.requirement.id for m in reasoner.received_gaps}
        assert sent_ids == {"java", "python"}


class TestDisabledOrFailingAI:
    def test_no_reasoner_available_returns_detail_unchanged(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        detail = ExperienceFitDetail(requirement_matches=[_match("java", MatchType.UNSUPPORTED)])
        updated = apply_evidence_reasoning("Job", "desc", detail, _index(), reasoner=None)
        assert updated == detail

    def test_reasoner_returning_empty_list_leaves_detail_unchanged(self):
        detail = ExperienceFitDetail(requirement_matches=[_match("java", MatchType.UNSUPPORTED)])
        reasoner = FakeReasoner([])
        updated = apply_evidence_reasoning("Job", "desc", detail, _index(), reasoner)
        assert updated.requirement_matches[0].ai_assessment is None

    def test_provider_failure_inside_reasoner_does_not_propagate(self):
        # apply_evidence_reasoning wraps the call defensively — any
        # reasoner implementation (not just the built-in Claude one) that
        # raises must never take down job evaluation.
        class BrokenReasoner(EvidenceReasoner):
            def reason_about_gaps(self, *a, **kw):
                raise RuntimeError("provider is down")

        detail = ExperienceFitDetail(requirement_matches=[_match("java", MatchType.UNSUPPORTED)])
        updated = apply_evidence_reasoning("Job", "desc", detail, _index(), BrokenReasoner())
        assert updated == detail
