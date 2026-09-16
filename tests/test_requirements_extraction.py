from datetime import datetime, timezone

from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.job import NormalizedJob
from careers_os.domain.requirements import RequirementImportance
from careers_os.domain.taxonomy import SkillCategory
from careers_os.scoring.requirements import extract_requirements


def _job(description: str, title: str = "Platform Engineer") -> NormalizedJob:
    return NormalizedJob(
        source="test",
        source_job_id="1",
        source_url="https://example.com/1",
        title=title,
        employment_type=EmploymentType.FULL_TIME,
        remote_status=RemoteStatus.REMOTE,
        retrieved_at=datetime.now(timezone.utc),
        description=description,
    )


class TestSkillExtraction:
    def test_finds_taxonomy_skills_in_description(self):
        job = _job("Must have experience with Kubernetes and AWS.")
        reqs = extract_requirements(job, SkillsTaxonomy.load())
        skills = {r.canonical_skill for r in reqs}
        assert "kubernetes" in skills
        assert "aws" in skills

    def test_skills_not_present_are_not_extracted(self):
        job = _job("Must have experience with Kubernetes.")
        reqs = extract_requirements(job, SkillsTaxonomy.load())
        skills = {r.canonical_skill for r in reqs}
        assert "python" not in skills


class TestPreferredVsRequired:
    def test_skill_before_preferred_marker_is_required(self):
        job = _job("Required: AWS experience. Preferred Qualifications: Kubernetes experience.")
        reqs = {r.canonical_skill: r for r in extract_requirements(job, SkillsTaxonomy.load())}
        assert reqs["aws"].is_preferred is False

    def test_skill_after_preferred_marker_is_preferred(self):
        job = _job("Required: AWS experience. Preferred Qualifications: Kubernetes experience.")
        reqs = {r.canonical_skill: r for r in extract_requirements(job, SkillsTaxonomy.load())}
        assert reqs["kubernetes"].is_preferred is True


class TestYearsExtraction:
    def test_years_near_a_skill_attach_to_that_skill(self):
        job = _job("5+ years of AWS experience required.")
        reqs = {r.canonical_skill: r for r in extract_requirements(job, SkillsTaxonomy.load())}
        assert reqs["aws"].min_years == 5

    def test_generic_years_mention_with_no_nearby_skill_is_standalone(self):
        job = _job(
            "We need someone sharp. " + ("x " * 40) + "8+ years of relevant experience overall."
        )
        reqs = extract_requirements(job, SkillsTaxonomy.load())
        generic = [r for r in reqs if r.canonical_skill is None and r.min_years == 8]
        assert generic
        assert generic[0].category == SkillCategory.RESPONSIBILITY

    def test_no_years_mentioned_leaves_min_years_none(self):
        job = _job("AWS experience required.")
        reqs = {r.canonical_skill: r for r in extract_requirements(job, SkillsTaxonomy.load())}
        assert reqs["aws"].min_years is None


class TestEducation:
    def test_bachelors_degree_mention_extracted(self):
        job = _job("Bachelor's degree in Computer Science or equivalent experience required.")
        reqs = {r.canonical_skill for r in extract_requirements(job, SkillsTaxonomy.load())}
        assert "bachelors_degree" in reqs


class TestRequirementImportance:
    def test_years_requirement_in_minimum_requirements_section_is_hard_required(self):
        job = _job(
            "Minimum requirements 3+ years of experience as a Golang software engineer. "
            "Preferred qualifications Experience in PHP and Ruby."
        )
        reqs = {r.canonical_skill: r for r in extract_requirements(job, SkillsTaxonomy.load())}
        assert reqs["go"].importance == RequirementImportance.HARD_REQUIRED
        assert reqs["go"].min_years == 3

    def test_preferred_skill_is_never_hard_required_even_with_years(self):
        job = _job(
            "Minimum requirements 3+ years of Go experience. "
            "Preferred qualifications 5+ years of PHP experience."
        )
        reqs = {r.canonical_skill: r for r in extract_requirements(job, SkillsTaxonomy.load())}
        assert reqs["php"].importance == RequirementImportance.PREFERRED

    def test_skill_without_years_in_hard_section_is_required_not_hard_required(self):
        job = _job("Minimum requirements Experience with REST APIs.")
        reqs = {r.canonical_skill: r for r in extract_requirements(job, SkillsTaxonomy.load())}
        assert reqs["rest_api"].importance == RequirementImportance.REQUIRED

    def test_boilerplate_prose_mentioning_preferred_qualifications_does_not_mislabel_real_minimum_section(self):
        # Real-world case (Stripe's own posting boilerplate): "preferred
        # qualifications" appears in an earlier prose sentence *before* the
        # real "Minimum requirements" heading — the prose occurrence must
        # not be mistaken for the section boundary.
        job = _job(
            "Meets the minimum requirements to be considered. The preferred qualifications "
            "are a bonus, not a requirement. Minimum requirements 3+ years of Go experience. "
            "Preferred qualifications Experience in PHP."
        )
        reqs = {r.canonical_skill: r for r in extract_requirements(job, SkillsTaxonomy.load())}
        assert reqs["go"].importance == RequirementImportance.HARD_REQUIRED
        assert reqs["go"].is_preferred is False
        assert reqs["php"].is_preferred is True

    def test_every_requirement_has_a_stable_id(self):
        job = _job("5+ years of AWS experience required.")
        reqs = extract_requirements(job, SkillsTaxonomy.load())
        assert all(r.id for r in reqs)


class TestWhatYouNeedHeading:
    """Phase 4.1: Ashby-hosted postings (e.g. Ramp) use a "WHAT YOU NEED"
    heading for their required list."""

    def _hard(self, description):
        from careers_os.domain.requirements import RequirementImportance
        return {
            r.canonical_skill for r in extract_requirements(_job(description))
            if r.importance == RequirementImportance.HARD_REQUIRED
        }

    def test_years_threshold_under_what_you_need_is_hard_required(self):
        hard = self._hard(
            "ABOUT THE ROLE\nHelp customers integrate.\n\nWHAT YOU NEED\n"
            " - 3+ years of hands-on experience working with SAP S/4HANA, including finance modules"
        )
        assert "erp_systems" in hard

    def test_prose_mention_is_not_treated_as_a_heading(self):
        hard = self._hard(
            "We give you what you need to succeed. You'll use 3+ years of SAP S/4HANA experience daily."
        )
        assert "erp_systems" not in hard

    def test_skill_without_years_under_what_you_need_is_only_required(self):
        from careers_os.domain.requirements import RequirementImportance
        reqs = extract_requirements(_job("WHAT YOU NEED\n - Experience with SAP S/4HANA"))
        erp = next(r for r in reqs if r.canonical_skill == "erp_systems")
        assert erp.importance == RequirementImportance.REQUIRED
