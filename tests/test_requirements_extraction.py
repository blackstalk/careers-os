from datetime import datetime, timezone

from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.job import NormalizedJob
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
