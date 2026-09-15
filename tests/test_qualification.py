from careers_os.domain.experience import ExperienceFitDetail
from careers_os.domain.matching import EvidenceRef, MatchType, RequirementMatch
from careers_os.domain.qualification import QualificationStatus
from careers_os.domain.requirements import JobRequirement, RequirementImportance
from careers_os.domain.taxonomy import SkillCategory
from careers_os.scoring.qualification import compute_qualification


def _req(skill: str, importance: RequirementImportance) -> JobRequirement:
    return JobRequirement(id=skill, category=SkillCategory.PROGRAMMING_LANGUAGE, canonical_skill=skill, text=skill, importance=importance)


def _match(skill: str, importance: RequirementImportance, match_type: MatchType) -> RequirementMatch:
    refs = [EvidenceRef(evidence_id=f"ev-{skill}", statement="stmt")] if match_type != MatchType.UNSUPPORTED else []
    return RequirementMatch(requirement=_req(skill, importance), match_type=match_type, matched_evidence=refs, reason="test")


class TestHardRequirementGate:
    def test_unsupported_hard_requirement_fails_regardless_of_other_strong_matches(self):
        # Mirrors the real Stripe Backend Engineer case: strong PHP/API
        # matches must not rescue a hard-required, zero-evidence Go gap.
        detail = ExperienceFitDetail(requirement_matches=[
            _match("php", RequirementImportance.PREFERRED, MatchType.STRONG_MATCH),
            _match("rest_api", RequirementImportance.REQUIRED, MatchType.STRONG_MATCH),
            _match("integration", RequirementImportance.REQUIRED, MatchType.STRONG_MATCH),
            _match("go", RequirementImportance.HARD_REQUIRED, MatchType.UNSUPPORTED),
        ])
        result = compute_qualification(detail)
        assert result.status == QualificationStatus.FAIL
        assert len(result.hard_gaps) == 1
        assert result.hard_gaps[0].requirement.canonical_skill == "go"

    def test_adjacent_hard_requirement_is_weak_not_fail(self):
        detail = ExperienceFitDetail(requirement_matches=[
            _match("kubernetes", RequirementImportance.HARD_REQUIRED, MatchType.ADJACENT_EXPERIENCE),
        ])
        result = compute_qualification(detail)
        assert result.status == QualificationStatus.WEAK

    def test_strong_hard_requirement_does_not_fail(self):
        detail = ExperienceFitDetail(requirement_matches=[
            _match("aws", RequirementImportance.HARD_REQUIRED, MatchType.STRONG_MATCH),
        ])
        result = compute_qualification(detail)
        assert result.status == QualificationStatus.STRONG


class TestPreferredNeverGates:
    def test_unsupported_preferred_requirement_never_fails_qualification(self):
        detail = ExperienceFitDetail(requirement_matches=[
            _match("kubernetes", RequirementImportance.PREFERRED, MatchType.UNSUPPORTED),
            _match("php", RequirementImportance.REQUIRED, MatchType.STRONG_MATCH),
        ])
        result = compute_qualification(detail)
        assert result.status == QualificationStatus.STRONG

    def test_only_preferred_requirements_present_is_unknown_not_fail(self):
        detail = ExperienceFitDetail(requirement_matches=[
            _match("kubernetes", RequirementImportance.PREFERRED, MatchType.UNSUPPORTED),
        ])
        result = compute_qualification(detail)
        assert result.status == QualificationStatus.UNKNOWN


class TestNoResumeImported:
    def test_no_resume_is_unknown(self):
        detail = ExperienceFitDetail(requirement_matches=[], resume_available=False)
        result = compute_qualification(detail)
        assert result.status == QualificationStatus.UNKNOWN


class TestStrongCaseA:
    def test_strong_required_evidence_with_no_hard_requirements_is_strong(self):
        # Mirrors the real Creative Circle PHP case: excellent required
        # evidence, no hard-required numeric gates at all.
        detail = ExperienceFitDetail(requirement_matches=[
            _match("php", RequirementImportance.REQUIRED, MatchType.STRONG_MATCH),
        ])
        result = compute_qualification(detail)
        assert result.status == QualificationStatus.STRONG
