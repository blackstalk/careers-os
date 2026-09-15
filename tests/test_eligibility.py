from datetime import datetime, timezone

from careers_os.career.candidate import CandidateProfile
from careers_os.career.eligibility import evaluate_eligibility
from careers_os.domain.eligibility import ConstraintType, EligibilityStatus
from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.job import NormalizedJob


def _candidate(**overrides) -> CandidateProfile:
    base = CandidateProfile.load().model_dump()
    base.update(overrides)
    return CandidateProfile(**base)


def _job(title: str, description: str, remote_status: RemoteStatus = RemoteStatus.REMOTE) -> NormalizedJob:
    return NormalizedJob(
        source="test", source_job_id="1", source_url="https://example.com/1",
        title=title, description=description, remote_status=remote_status,
        employment_type=EmploymentType.CONTRACT, retrieved_at=datetime.now(timezone.utc),
    )


class TestNoConstraintsDetected:
    def test_plain_job_with_no_hard_constraints_is_eligible(self):
        job = _job("Senior Web Developer", "PHP development on a template-driven website.")
        result = evaluate_eligibility(job, _candidate())
        assert result.status == EligibilityStatus.ELIGIBLE
        assert result.checks == []


class TestTimezone:
    def test_ambiguous_timezone_phrasing_is_verify_not_ineligible(self):
        job = _job(
            "Technical Solutions Engineer",
            "This role is remote but requires candidates to be located in the Mountain or "
            "Pacific time zones.",
        )
        result = evaluate_eligibility(job, _candidate())
        assert result.status == EligibilityStatus.VERIFY
        assert result.checks[0].constraint_type == ConstraintType.TIMEZONE

    def test_matching_timezone_language_still_verifies_not_silently_passes(self):
        # Even when the candidate's timezone matches the named region, the
        # phrasing itself is inherently ambiguous (residency vs. hours) —
        # matching text alone should not silently resolve as clean-eligible.
        job = _job("Role", "Must be located in the Central time zone.")
        candidate = _candidate(location={"city": "Chicago", "state": "Illinois", "country": "US", "timezone": "America/Chicago"})
        result = evaluate_eligibility(job, candidate)
        assert result.status == EligibilityStatus.VERIFY


class TestUnambiguousResidency:
    def test_explicit_state_residency_mismatch_is_ineligible(self):
        job = _job("Role", "Must reside in California for this position.")
        result = evaluate_eligibility(job, _candidate())  # candidate is in Texas
        assert result.status == EligibilityStatus.INELIGIBLE
        assert result.checks[0].constraint_type == ConstraintType.GEOGRAPHIC_RESIDENCY

    def test_explicit_state_residency_match_is_eligible(self):
        job = _job("Role", "Must reside in Texas for this position.")
        result = evaluate_eligibility(job, _candidate())
        assert result.status == EligibilityStatus.ELIGIBLE


class TestWorkAuthorization:
    def test_no_sponsorship_language_with_authorized_candidate_is_eligible(self):
        job = _job(
            "Role",
            "You must be currently authorized to work in the United States without the need "
            "of employer sponsorship.",
        )
        result = evaluate_eligibility(job, _candidate())
        assert result.status == EligibilityStatus.ELIGIBLE

    def test_no_sponsorship_language_with_sponsorship_needed_candidate_is_ineligible(self):
        job = _job("Role", "Must be authorized to work in the United States without sponsorship.")
        candidate = _candidate(work_authorization={"country": "US", "authorized": False, "sponsorship_required": True})
        result = evaluate_eligibility(job, candidate)
        assert result.status == EligibilityStatus.INELIGIBLE


class TestSecurityClearance:
    def test_clearance_required_with_unknown_candidate_status_is_unknown(self):
        job = _job("Role", "Candidate must hold an active security clearance.")
        result = evaluate_eligibility(job, _candidate())  # security_clearance.held defaults to None
        assert result.status == EligibilityStatus.UNKNOWN

    def test_clearance_required_and_held_is_eligible(self):
        job = _job("Role", "Candidate must hold an active security clearance.")
        candidate = _candidate(security_clearance={"held": True})
        result = evaluate_eligibility(job, candidate)
        assert result.status == EligibilityStatus.ELIGIBLE

    def test_clearance_required_and_not_held_is_ineligible(self):
        job = _job("Role", "Candidate must hold an active security clearance.")
        candidate = _candidate(security_clearance={"held": False})
        result = evaluate_eligibility(job, candidate)
        assert result.status == EligibilityStatus.INELIGIBLE


class TestRelocation:
    def test_relocation_required_with_unwilling_candidate_is_ineligible(self):
        job = _job("Role", "This position requires relocation to our headquarters.")
        result = evaluate_eligibility(job, _candidate())  # relocation.willing defaults False
        assert result.status == EligibilityStatus.INELIGIBLE

    def test_relocation_required_with_willing_candidate_is_eligible(self):
        job = _job("Role", "This position requires relocation to our headquarters.")
        candidate = _candidate(relocation={"willing": True})
        result = evaluate_eligibility(job, candidate)
        assert result.status == EligibilityStatus.ELIGIBLE


class TestWorkArrangement:
    def test_onsite_role_with_unacceptable_preference_is_ineligible(self):
        job = _job("Role", "Onsite role.", remote_status=RemoteStatus.ONSITE)
        candidate = _candidate(work_preferences={"remote": "preferred", "hybrid": "acceptable", "onsite": "unacceptable"})
        result = evaluate_eligibility(job, candidate)
        assert result.status == EligibilityStatus.INELIGIBLE

    def test_onsite_role_with_acceptable_preference_is_eligible(self):
        job = _job("Role", "Onsite role.", remote_status=RemoteStatus.ONSITE)
        result = evaluate_eligibility(job, _candidate())  # default onsite=acceptable
        assert result.status == EligibilityStatus.ELIGIBLE

    def test_remote_job_triggers_no_work_arrangement_check(self):
        job = _job("Role", "Fully remote role.", remote_status=RemoteStatus.REMOTE)
        result = evaluate_eligibility(job, _candidate())
        assert not any(c.constraint_type == ConstraintType.WORK_ARRANGEMENT for c in result.checks)

    def test_unknown_remote_status_triggers_no_check(self):
        job = _job("Role", "Some role.", remote_status=RemoteStatus.UNKNOWN)
        result = evaluate_eligibility(job, _candidate())
        assert not any(c.constraint_type == ConstraintType.WORK_ARRANGEMENT for c in result.checks)


class TestMultipleConstraintsRollup:
    def test_ineligible_dominates_over_verify(self):
        job = _job(
            "Role",
            "Must reside in California. Also requires relocation to our headquarters.",
        )
        result = evaluate_eligibility(job, _candidate())  # TX candidate, unwilling to relocate
        assert result.status == EligibilityStatus.INELIGIBLE
        assert len(result.checks) == 2
