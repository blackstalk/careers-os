from careers_os.career.resume_recommendation import recommend_resume_variant
from careers_os.domain.experience import ExperienceFitDetail
from careers_os.domain.matching import EvidenceRef, MatchType, RequirementMatch
from careers_os.domain.requirements import JobRequirement
from careers_os.domain.taxonomy import SkillCategory


def _match(skill: str, match_type: MatchType, variant_slug: str | None) -> RequirementMatch:
    refs = (
        [EvidenceRef(evidence_id=f"ev-{skill}", statement=f"did {skill}", variant_slug=variant_slug)]
        if variant_slug
        else []
    )
    return RequirementMatch(
        requirement=JobRequirement(category=SkillCategory.CLOUD, canonical_skill=skill, text=skill),
        match_type=match_type,
        matched_evidence=refs,
        reason="test",
    )


class TestRecommendResumeVariant:
    def test_variant_with_more_strong_matches_wins(self):
        detail = ExperienceFitDetail(
            requirement_matches=[
                _match("aws", MatchType.STRONG_MATCH, "fde"),
                _match("docker", MatchType.STRONG_MATCH, "fde"),
                _match("php", MatchType.STRONG_MATCH, "senior_dev"),
            ]
        )
        rec = recommend_resume_variant(detail)
        assert rec.recommended_variant == "fde"
        assert rec.scores["fde"] > rec.scores["senior_dev"]

    def test_partial_matches_weighted_less_than_strong(self):
        detail = ExperienceFitDetail(
            requirement_matches=[
                _match("aws", MatchType.PARTIAL_MATCH, "senior_dev"),
                _match("docker", MatchType.STRONG_MATCH, "fde"),
            ]
        )
        rec = recommend_resume_variant(detail)
        assert rec.recommended_variant == "fde"

    def test_no_cited_evidence_yields_no_recommendation(self):
        detail = ExperienceFitDetail(
            requirement_matches=[_match("kubernetes", MatchType.UNSUPPORTED, None)]
        )
        rec = recommend_resume_variant(detail)
        assert rec.recommended_variant is None
        assert "No resume variant" in rec.note

    def test_reasons_list_the_requirements_that_tipped_the_decision(self):
        detail = ExperienceFitDetail(
            requirement_matches=[_match("aws", MatchType.STRONG_MATCH, "fde")]
        )
        rec = recommend_resume_variant(detail)
        assert "aws" in rec.reasons["fde"]
