"""AI guardrail tests.

Phase 2's experience_fit / evidence matching is entirely deterministic —
no AI is used to decide whether a job requirement is matched, partially
matched, or unsupported (see docs/evidence-model.md#ai-guardrails). These
tests enforce that as a structural invariant, and confirm the existing
Phase 1 AI layer (scoring/ai.py, which only ever refines role_fit /
career_direction_fit) still degrades safely with no API key and never
touches experience data.
"""

from datetime import date, datetime, timezone

from careers_os.career.preferences import Preferences
from careers_os.career.profile import CareerProfile
from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.evidence import Evidence, EvidenceProvenance
from careers_os.domain.job import NormalizedJob
from careers_os.domain.resume import CareerRole
from careers_os.domain.taxonomy import SkillCategory
from careers_os.scoring import ai
from careers_os.scoring.engine import score_job
from careers_os.scoring.evidence_matcher import EvidenceIndex


def _job(description: str) -> NormalizedJob:
    return NormalizedJob(
        source="test",
        source_job_id="1",
        source_url="https://example.com/1",
        title="Platform Engineer",
        employment_type=EmploymentType.FULL_TIME,
        remote_status=RemoteStatus.REMOTE,
        retrieved_at=datetime.now(timezone.utc),
        description=description,
    )


def _evidence_index() -> EvidenceIndex:
    role = CareerRole(id="acme-2020", company="Acme", title="Engineer", start_date=date(2020, 1, 1))
    evidence = Evidence(
        id="ev-1",
        type=SkillCategory.CLOUD,
        statement="Built AWS infrastructure.",
        canonical_skills=["aws"],
        provenance=EvidenceProvenance(
            source_type="resume", source_name="fde", company="Acme",
            career_role_id=role.id, variant_slug="fde", extracted_at=datetime.now(timezone.utc),
        ),
        start_date=role.start_date,
    )
    return EvidenceIndex(evidence=[evidence], career_roles=[role], taxonomy=SkillsTaxonomy.load())


class TestModuleBoundary:
    def test_evidence_matcher_module_does_not_import_ai_module(self):
        import careers_os.scoring.evidence_matcher as evidence_matcher

        assert "ai" not in evidence_matcher.__dict__ or evidence_matcher.__dict__["ai"] is not ai


class TestNoKeyDegradesGracefully:
    def test_ai_unavailable_returns_none(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert ai.is_available() is False
        profile = CareerProfile.load()
        job = _job("Some AWS work.")
        assert ai.evaluate(job, profile) is None

    def test_score_job_with_evidence_and_no_ai_key_is_fully_deterministic(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        job = _job("AWS experience required.")
        profile = CareerProfile.load()
        preferences = Preferences.load()
        result = score_job(job, profile, preferences, use_ai=True, evidence_index=_evidence_index())

        assert result.ai_evaluation_included is False
        assert result.experience_detail is not None
        assert result.experience_fit.score > 0  # real evidence-based score, not the 0.5 placeholder


class TestAICannotTouchExperienceFit:
    def test_apply_ai_evaluation_only_modifies_role_and_direction_fit(self):
        job = _job("AWS experience required.")
        profile = CareerProfile.load()
        preferences = Preferences.load()
        result = score_job(job, profile, preferences, use_ai=False, evidence_index=_evidence_index())
        original_experience_fit = result.experience_fit.model_copy()
        original_experience_detail = result.experience_detail.model_copy()

        # Even a maximally aggressive fake AI result (attempting to also
        # override experience-related fields) can only affect the two
        # fields apply_ai_evaluation actually reads.
        fake_ai_result = {
            "role_fit_score": 0.99,
            "role_fit_reason": "ai says so",
            "career_direction_score": 0.99,
            "career_direction_reason": "ai says so",
            "experience_fit_score": 0.99,  # not a real field — must be ignored
            "gaps": [],  # not a real field — must be ignored
        }
        updated = ai.apply_ai_evaluation(result, fake_ai_result)

        assert updated.experience_fit == original_experience_fit
        assert updated.experience_detail == original_experience_detail
        assert updated.role_fit.score == 0.99
