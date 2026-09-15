from datetime import date, datetime, timezone

from careers_os.career.skills import SkillsTaxonomy
from careers_os.career.timeline import merge_intervals, total_years, years_for_skill
from careers_os.domain.evidence import Evidence, EvidenceProvenance
from careers_os.domain.matching import GapType, MatchType
from careers_os.domain.requirements import JobRequirement
from careers_os.domain.resume import CareerRole
from careers_os.domain.taxonomy import SkillCategory
from careers_os.scoring.evidence_matcher import EvidenceIndex, match_requirement


def _evidence(canonical_skills, *, start=None, end=None, company="Acme", statement="stmt") -> Evidence:
    return Evidence(
        id=f"ev-{statement}-{canonical_skills}",
        type=SkillCategory.ARCHITECTURE,
        statement=statement,
        canonical_skills=canonical_skills,
        provenance=EvidenceProvenance(
            source_type="resume",
            source_name="test",
            company=company,
            variant_slug="fde",
            extracted_at=datetime.now(timezone.utc),
        ),
        start_date=start,
        end_date=end,
    )


def _role(company, start, end=None) -> CareerRole:
    return CareerRole(
        id=f"{company}-{start.isoformat()}", company=company, title="Engineer",
        start_date=start, end_date=end,
    )


class TestTimelineOverlap:
    def test_merge_intervals_combines_overlapping_ranges(self):
        merged = merge_intervals([
            (date(2020, 1, 1), date(2022, 1, 1)),
            (date(2021, 1, 1), date(2023, 1, 1)),
        ])
        assert merged == [(date(2020, 1, 1), date(2023, 1, 1))]

    def test_overlapping_concurrent_roles_are_not_double_counted(self):
        # Two roles covering the identical 2-year window should contribute
        # 2 years total, not 4.
        intervals = [
            (date(2020, 1, 1), date(2022, 1, 1)),
            (date(2020, 1, 1), date(2022, 1, 1)),
        ]
        assert total_years(intervals) == 2.0

    def test_non_overlapping_intervals_sum_normally(self):
        intervals = [
            (date(2010, 1, 1), date(2012, 1, 1)),
            (date(2015, 1, 1), date(2017, 1, 1)),
        ]
        assert total_years(intervals) == 4.0

    def test_years_for_skill_merges_across_overlapping_dated_evidence(self):
        role_a = _role("Acme", date(2020, 1, 1))
        role_b = _role("Consulting Co", date(2020, 1, 1), date(2022, 1, 1))
        evidence = [
            _evidence(["aws"], start=date(2020, 1, 1), end=None, company="Acme"),
            _evidence(["aws"], start=date(2020, 1, 1), end=date(2022, 1, 1), company="Consulting Co"),
        ]
        estimate = years_for_skill("aws", evidence, [role_a, role_b], as_of=date(2022, 1, 1))
        assert estimate.numeric_years == 2.0  # not 4 — overlap merged
        assert set(estimate.supporting_companies) == {"Acme", "Consulting Co"}

    def test_undated_evidence_only_yields_low_confidence_unknown_years(self):
        evidence = [_evidence(["aws"], start=None, end=None)]
        estimate = years_for_skill("aws", evidence, [])
        assert estimate.numeric_years == 0.0
        assert estimate.confidence < 0.2

    def test_no_evidence_at_all_yields_zero_confidence(self):
        estimate = years_for_skill("kubernetes", [], [])
        assert estimate.confidence == 0.0


class TestMatchRequirement:
    def _index(self, evidence, roles=None):
        return EvidenceIndex(evidence=evidence, career_roles=roles or [], taxonomy=SkillsTaxonomy.load())

    def test_direct_evidence_yields_strong_match(self):
        index = self._index([_evidence(["aws"], start=date(2020, 1, 1))])
        req = JobRequirement(category=SkillCategory.CLOUD, canonical_skill="aws", text="AWS")
        result = match_requirement(req, index)
        assert result.match_type == MatchType.STRONG_MATCH
        assert result.gap_type is None
        assert result.matched_evidence

    def test_two_adjacent_skills_yield_partial_match(self):
        # kubernetes' adjacency list includes docker, ci_cd, aws, linux.
        index = self._index([
            _evidence(["docker"], start=date(2020, 1, 1)),
            _evidence(["ci_cd"], start=date(2020, 1, 1)),
        ])
        req = JobRequirement(category=SkillCategory.CLOUD, canonical_skill="kubernetes", text="Kubernetes")
        result = match_requirement(req, index)
        assert result.match_type == MatchType.PARTIAL_MATCH
        assert result.gap_type == GapType.RESUME_LANGUAGE_GAP

    def test_one_adjacent_skill_yields_adjacent_experience(self):
        index = self._index([_evidence(["docker"], start=date(2020, 1, 1))])
        req = JobRequirement(category=SkillCategory.CLOUD, canonical_skill="kubernetes", text="Kubernetes")
        result = match_requirement(req, index)
        assert result.match_type == MatchType.ADJACENT_EXPERIENCE
        assert result.gap_type == GapType.INTERVIEW_PREP_GAP

    def test_no_evidence_at_all_yields_unsupported_never_inferred(self):
        index = self._index([])
        req = JobRequirement(category=SkillCategory.CLOUD, canonical_skill="kubernetes", text="Kubernetes")
        result = match_requirement(req, index)
        assert result.match_type == MatchType.UNSUPPORTED
        assert result.gap_type == GapType.REAL_EXPERIENCE_GAP
        assert result.matched_evidence == []

    def test_requirement_with_no_taxonomy_mapping_is_unknown_not_guessed(self):
        index = self._index([_evidence(["aws"], start=date(2020, 1, 1))])
        req = JobRequirement(category=SkillCategory.RESPONSIBILITY, canonical_skill=None, text="Something vague")
        result = match_requirement(req, index)
        assert result.match_type == MatchType.UNKNOWN
        assert result.gap_type == GapType.UNKNOWN

    def test_years_gated_requirement_demotes_to_partial_when_duration_insufficient(self):
        index = self._index(
            [_evidence(["aws"], start=date(2023, 1, 1))],
            roles=[_role("Acme", date(2023, 1, 1))],
        )
        req = JobRequirement(
            category=SkillCategory.CLOUD, canonical_skill="aws", text="AWS", min_years=5
        )
        result = match_requirement(req, index)
        assert result.match_type == MatchType.PARTIAL_MATCH  # skill present, just not long enough

    def test_evidence_never_promoted_across_categories_beyond_adjacency_list(self):
        # "python" has no configured adjacency in the taxonomy — evidence of
        # unrelated skills must never produce a match for it.
        index = self._index([_evidence(["php"], start=date(2020, 1, 1))])
        req = JobRequirement(category=SkillCategory.PROGRAMMING_LANGUAGE, canonical_skill="python", text="Python")
        result = match_requirement(req, index)
        assert result.match_type == MatchType.UNSUPPORTED
