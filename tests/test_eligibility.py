from datetime import datetime, timezone

import pytest

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


class TestClearanceWording:
    @pytest.mark.parametrize("text", [
        "Appian Solution Architect - Clearance Required",
        "Active Secret clearance. U.S. citizenship required.",
        "Clearance and Access: this position requires U.S. citizenship and CAC eligibility.",
        "Ability to obtain and maintain a DHS Public Trust.",
    ])
    def test_government_contractor_phrasings_are_not_silently_eligible(self, text):
        # Phase 4.3: these reached strong_pursue from aggregator postings.
        job = _job("Role", text)
        assert evaluate_eligibility(job, _candidate()).status == EligibilityStatus.UNKNOWN


    def test_public_trust_in_ordinary_prose_is_not_a_clearance(self):
        job = _job("Staff Forward Deployed Engineer", "Step in when executive confidence or public trust is at risk.")
        assert evaluate_eligibility(job, _candidate()).status == EligibilityStatus.ELIGIBLE


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
        # Explicit override rather than relying on the real candidate.yaml
        # default, which is "unacceptable" for onsite/hybrid (Phase 4.1 —
        # the candidate's current search is remote-only).
        candidate = _candidate(work_preferences={"remote": "preferred", "hybrid": "unacceptable", "onsite": "acceptable"})
        result = evaluate_eligibility(job, candidate)
        assert result.status == EligibilityStatus.ELIGIBLE

    def test_remote_job_triggers_no_work_arrangement_check(self):
        job = _job("Role", "Fully remote role.", remote_status=RemoteStatus.REMOTE)
        result = evaluate_eligibility(job, _candidate())
        assert not any(c.constraint_type == ConstraintType.WORK_ARRANGEMENT for c in result.checks)

    def test_unknown_remote_status_triggers_no_check_when_other_arrangements_are_acceptable(self):
        job = _job("Role", "Some role.", remote_status=RemoteStatus.UNKNOWN)
        candidate = _candidate(work_preferences={"remote": "preferred", "hybrid": "acceptable", "onsite": "unacceptable"})
        result = evaluate_eligibility(job, candidate)
        assert not any(c.constraint_type == ConstraintType.WORK_ARRANGEMENT for c in result.checks)

    def test_hybrid_role_with_unacceptable_preference_is_ineligible(self):
        # Phase 4.1 production case: hybrid/onsite postings must not
        # clear eligibility purely because technical/direction fit is
        # strong elsewhere — this is the actual real candidate.yaml
        # default now (remote-only search).
        job = _job("Role", "Hybrid role.", remote_status=RemoteStatus.HYBRID)
        result = evaluate_eligibility(job, _candidate())  # real candidate.yaml: hybrid=unacceptable
        assert result.status == EligibilityStatus.INELIGIBLE
        assert result.checks[0].constraint_type == ConstraintType.WORK_ARRANGEMENT

    def test_hybrid_role_with_acceptable_preference_is_eligible(self):
        job = _job("Role", "Hybrid role.", remote_status=RemoteStatus.HYBRID)
        candidate = _candidate(work_preferences={"remote": "preferred", "hybrid": "acceptable", "onsite": "unacceptable"})
        result = evaluate_eligibility(job, candidate)
        assert result.status == EligibilityStatus.ELIGIBLE

    def test_explicitly_remote_role_is_eligible_with_real_candidate_defaults(self):
        job = _job("Role", "Fully remote role.", remote_status=RemoteStatus.REMOTE)
        result = evaluate_eligibility(job, _candidate())
        assert result.status == EligibilityStatus.ELIGIBLE

    def test_unknown_arrangement_is_not_silently_treated_as_remote(self):
        # Real candidate.yaml is remote-only: an unstated arrangement is
        # neither a pass nor a rejection — it must be verified.
        job = _job("Role", "Some role with no stated work arrangement.", remote_status=RemoteStatus.UNKNOWN)
        result = evaluate_eligibility(job, _candidate())
        assert result.status == EligibilityStatus.VERIFY
        assert result.checks[0].constraint_type == ConstraintType.WORK_ARRANGEMENT

    def test_geographically_constrained_remote_role_uses_existing_residency_check(self):
        # "Remote" alongside a residency requirement is already handled
        # by the existing GEOGRAPHIC_RESIDENCY check, entirely separate
        # from the WORK_ARRANGEMENT check — a remote role does not get a
        # free pass on residency constraints just for being remote.
        job = _job(
            "Role", "Remote — must reside in California for this position.",
            remote_status=RemoteStatus.REMOTE,
        )
        result = evaluate_eligibility(job, _candidate())  # candidate is in Texas
        assert result.status == EligibilityStatus.INELIGIBLE
        assert result.checks[0].constraint_type == ConstraintType.GEOGRAPHIC_RESIDENCY

    def test_geographically_constrained_remote_role_matching_candidate_is_eligible(self):
        job = _job(
            "Role", "Remote — must reside in Texas for this position.",
            remote_status=RemoteStatus.REMOTE,
        )
        result = evaluate_eligibility(job, _candidate())
        assert result.status == EligibilityStatus.ELIGIBLE


class TestRemoteLocationGeography:
    def test_location_field_naming_a_country_is_verify_not_silently_eligible(self):
        job = _job("Role", "Fully remote role.", remote_status=RemoteStatus.REMOTE)
        job = job.model_copy(update={"location": "India - Remote"})
        result = evaluate_eligibility(job, _candidate())
        assert result.status == EligibilityStatus.VERIFY
        assert result.checks[0].constraint_type == ConstraintType.GEOGRAPHIC_RESIDENCY

    def test_plain_remote_with_no_country_in_location_is_unaffected(self):
        job = _job("Role", "Fully remote role.", remote_status=RemoteStatus.REMOTE)
        job = job.model_copy(update={"location": "Remote"})
        result = evaluate_eligibility(job, _candidate())
        assert result.status == EligibilityStatus.ELIGIBLE

    def test_us_remote_location_is_unaffected(self):
        job = _job("Role", "Fully remote role.", remote_status=RemoteStatus.REMOTE)
        job = job.model_copy(update={"location": "United States - Remote"})
        result = evaluate_eligibility(job, _candidate())
        assert result.status == EligibilityStatus.ELIGIBLE

    @pytest.mark.parametrize(("title", "location"), [
        ("Senior Solutions Architect - Europe", "EU | Remote"),
        ("Senior Pre-Sales Solutions Engineer (Australia)", "Sydney, Australia"),
        ("Sr AI Engineer | Remote - Europe | TS/Vue/NodeJS", "Berlin Office"),
        ("Solutions Engineer", "London"),
        ("Forward Deployed Engineer (Europe)", None),
        ("Forward Deployed Engineer, Agentic Platform (Korea)", "Seoul"),
    ])
    def test_region_or_hub_city_without_the_word_remote_is_verify(self, title, location):
        # Seen on remote-first Ashby boards (Phase 4.2): remote-flagged
        # postings whose only scope signal is a hub city, "EU", or a
        # region in the title.
        job = _job(title, "Fully remote role.", remote_status=RemoteStatus.REMOTE)
        job = job.model_copy(update={"location": location})
        assert evaluate_eligibility(job, _candidate()).status == EligibilityStatus.VERIFY

    @pytest.mark.parametrize(("title", "location"), [
        ("Technical Account Manager (US)", "United States"),
        ("Staff AI Engineer | US | Remote", "Remote"),
        ("Solutions Engineer (US/Canada)", "Toronto"),
        ("Solutions Engineer (Texas)", "Dallas, TX"),
        ("Senior Engineer", "United States, United Kingdom - Remote"),
        ("Senior Engineer", "Remote (Worldwide)"),
    ])
    def test_us_inclusive_or_unscoped_remote_is_unaffected(self, title, location):
        job = _job(title, "Fully remote role.", remote_status=RemoteStatus.REMOTE)
        job = job.model_copy(update={"location": location})
        assert evaluate_eligibility(job, _candidate()).status == EligibilityStatus.ELIGIBLE

    def test_non_remote_job_is_never_checked_for_location_geography(self):
        # A hybrid/onsite role with a foreign-sounding location string is
        # already caught by the work-arrangement check — this check must
        # not double up or fire for non-remote postings at all.
        job = _job("Role", "Hybrid role.", remote_status=RemoteStatus.HYBRID)
        job = job.model_copy(update={"location": "India"})
        result = evaluate_eligibility(job, _candidate())
        assert not any(c.constraint_type == ConstraintType.GEOGRAPHIC_RESIDENCY for c in result.checks)


class TestMultipleConstraintsRollup:
    def test_ineligible_dominates_over_verify(self):
        job = _job(
            "Role",
            "Must reside in California. Also requires relocation to our headquarters.",
        )
        result = evaluate_eligibility(job, _candidate())  # TX candidate, unwilling to relocate
        assert result.status == EligibilityStatus.INELIGIBLE
        assert len(result.checks) == 2


class TestCompensationReporting:
    """Phase 4.4: Greenhouse doesn't report employment type, so a posted
    salary was scored as "unknown" and alerts said "Compensation is not
    published" next to a visible range."""

    def _job(self, **kw):
        return NormalizedJob(
            source="t", source_job_id="1", source_url="https://e.com/1", title="Staff Engineer",
            description="Role.", retrieved_at=datetime.now(timezone.utc), **kw,
        )

    def test_published_salary_counts_even_without_an_employment_type(self):
        from careers_os.career.preferences import Preferences
        from careers_os.scoring.deterministic import score_compensation_fit

        comp = score_compensation_fit(self._job(salary_min=126400, salary_max=213600), Preferences.load())
        # Scored from the bottom of the range, not the top (Phase 5): the
        # minimum is what the posting actually guarantees.
        assert 0.6 < comp.score < 1.0
        assert comp.confidence >= 0.3  # i.e. not reported as "not published"
        assert "from $126,400" in comp.reason
        assert "Employment type isn't stated" in comp.reason

    def test_missing_salary_is_still_unknown(self):
        from careers_os.career.preferences import Preferences
        from careers_os.scoring.deterministic import score_compensation_fit

        comp = score_compensation_fit(self._job(), Preferences.load())
        assert comp.confidence < 0.3


class TestCompensationCertainty:
    """Phase 5: the replacement objective needs "is $120K+ base confirmed?",
    which a 0-1 score cannot answer. See domain/compensation.py."""

    def _job(self, **kw):
        from careers_os.domain.enums import EmploymentType
        kw.setdefault("employment_type", EmploymentType.FULL_TIME)
        return NormalizedJob(
            source="t", source_job_id="1", source_url="https://e.com/1", title="Full Stack Engineer",
            description=kw.pop("description", "Build web applications."),
            retrieved_at=datetime.now(timezone.utc), **kw,
        )

    def _assess(self, **kw):
        from careers_os.career.preferences import Preferences
        from careers_os.scoring.deterministic import assess_compensation
        return assess_compensation(self._job(**kw), Preferences.load())

    def test_range_entirely_above_the_floor_is_confirmed(self):
        from careers_os.domain.compensation import CompensationStatus
        a = self._assess(salary_min=125000, salary_max=150000)
        assert a.status == CompensationStatus.CONFIRMED_ABOVE and a.confirmed

    def test_range_starting_exactly_at_the_floor_is_confirmed(self):
        from careers_os.domain.compensation import CompensationStatus
        a = self._assess(salary_min=120000, salary_max=150000)
        assert a.status == CompensationStatus.CONFIRMED_AT and a.confirmed

    def test_range_straddling_the_floor_is_not_confirmed(self):
        from careers_os.domain.compensation import CompensationStatus
        a = self._assess(salary_min=100000, salary_max=130000)
        assert a.status == CompensationStatus.OVERLAPS_THRESHOLD
        assert not a.confirmed and a.needs_verification
        assert "would not clear it" in a.reason

    def test_range_below_the_floor_is_below_threshold(self):
        from careers_os.domain.compensation import CompensationStatus
        a = self._assess(salary_min=90000, salary_max=115000)
        assert a.status == CompensationStatus.BELOW_THRESHOLD
        assert not a.confirmed and not a.needs_verification

    def test_unpublished_salary_is_unknown_not_failed(self):
        from careers_os.domain.compensation import CompensationStatus
        a = self._assess()
        assert a.status == CompensationStatus.UNKNOWN and a.needs_verification

    def test_ote_language_prevents_a_confirmed_base_claim(self):
        from careers_os.domain.compensation import CompensationBasis, CompensationStatus
        a = self._assess(salary_min=150000, salary_max=150000,
                         description="Compensation is $150,000 OTE, base plus commission.")
        assert a.status == CompensationStatus.BASIS_UNCERTAIN
        assert a.basis == CompensationBasis.UNCERTAIN and "ote" in a.signals

    def test_contract_hourly_is_judged_separately(self):
        from careers_os.domain.compensation import CompensationBasis, CompensationStatus
        from careers_os.domain.enums import EmploymentType
        a = self._assess(employment_type=EmploymentType.CONTRACT, hourly_min=95, hourly_max=120)
        assert a.status == CompensationStatus.NON_SALARY and a.basis == CompensationBasis.HOURLY

    def test_contract_hourly_scoring_is_unchanged(self):
        from careers_os.career.preferences import Preferences
        from careers_os.domain.enums import EmploymentType
        from careers_os.scoring.deterministic import score_compensation_fit
        comp = score_compensation_fit(
            self._job(employment_type=EmploymentType.CONTRACT, hourly_min=95, hourly_max=120), Preferences.load())
        assert comp.score >= 0.8 and "hourly rate" in comp.reason


class TestCompensationBasisIsRangeLocal:
    """Phase 7: basis belongs to a figure, not to the document.

    A posting that labels its range "Anticipated Base Salary Range" and
    later mentions total compensation is publishing a base salary. The
    Phase 5 implementation scanned the whole description, so that
    boilerplate made 70 of 77 explicitly-labelled base ranges uncertain.
    """

    def _assess(self, description, **kw):
        from careers_os.career.preferences import Preferences
        from careers_os.domain.enums import EmploymentType
        from careers_os.scoring.deterministic import assess_compensation
        kw.setdefault("employment_type", EmploymentType.FULL_TIME)
        job = NormalizedJob(
            source="t", source_job_id="1", source_url="https://e.com/1", title="Full Stack Engineer",
            description=description, retrieved_at=datetime.now(timezone.utc), **kw,
        )
        return assess_compensation(job, Preferences.load())

    def test_1_explicit_base_salary_range_is_confirmed_base(self):
        from careers_os.domain.compensation import CompensationBasis, CompensationStatus
        a = self._assess("Base Salary Range $130,000 - $160,000 USD.",
                         salary_min=130000, salary_max=160000)
        assert a.status == CompensationStatus.CONFIRMED_ABOVE
        assert a.basis == CompensationBasis.BASE_SALARY
        assert any("base salary" in e for e in a.basis_evidence)

    def test_2_base_range_survives_later_total_compensation_boilerplate(self):
        """Upstart's real shape: the exact false uncertainty Phase 7 fixes."""
        from careers_os.domain.compensation import CompensationStatus
        a = self._assess(
            "United States | Remote - Anticipated Base Salary Range $142,000 - $196,600 USD. "
            "At Upstart, your base pay is one part of your total compensation package. The anticipated "
            "base salary for this position is expected to be within the below range. In addition, Upstart "
            "provides employees with target bonuses, equity compensation, and generous benefits packages.",
            salary_min=142000, salary_max=196600)
        assert a.status == CompensationStatus.CONFIRMED_ABOVE and a.confirmed

    def test_3_base_range_straddling_the_floor_still_overlaps(self):
        from careers_os.domain.compensation import CompensationStatus
        a = self._assess("The base salary range for this role is $100,000 - $130,000.",
                         salary_min=100000, salary_max=130000)
        assert a.status == CompensationStatus.OVERLAPS_THRESHOLD and not a.confirmed

    def test_4_ote_range_is_not_confirmed_base(self):
        from careers_os.domain.compensation import CompensationBasis, CompensationStatus
        a = self._assess("OTE $150,000 - $200,000 for this territory.",
                         salary_min=150000, salary_max=200000)
        assert a.status == CompensationStatus.BASIS_UNCERTAIN
        assert a.basis == CompensationBasis.UNCERTAIN and not a.basis_evidence

    def test_5_total_compensation_range_is_not_confirmed_base(self):
        from careers_os.domain.compensation import CompensationStatus
        a = self._assess("Expected total compensation range: $150,000 - $200,000.",
                         salary_min=150000, salary_max=200000)
        assert a.status == CompensationStatus.BASIS_UNCERTAIN and not a.confirmed

    def test_6_figure_with_no_basis_language_stays_uncertain_by_fallback(self):
        """No local label and no global phrase: Phase 5 called this base.
        That behaviour is deliberately preserved rather than widened."""
        from careers_os.domain.compensation import CompensationStatus
        a = self._assess("We pay $150,000 - $200,000 for this role.",
                         salary_min=150000, salary_max=200000)
        assert a.status == CompensationStatus.CONFIRMED_ABOVE
        assert a.basis_evidence == []  # confirmed by the Phase 5 fallback, not by local evidence

    def test_7_base_range_plus_separate_bonus_discussion_stays_confirmed(self):
        from careers_os.domain.compensation import CompensationStatus
        a = self._assess(
            "Base Salary Range $150,000 - $180,000. Employees are also eligible for an annual bonus, "
            "equity in the form of RSUs, and a comprehensive benefits package including medical and 401k.",
            salary_min=150000, salary_max=180000)
        assert a.status == CompensationStatus.CONFIRMED_ABOVE and a.confirmed

    def test_8_base_range_alongside_a_separate_ote_figure_uses_the_base_range(self):
        from careers_os.domain.compensation import CompensationStatus
        a = self._assess(
            "Base Salary Range $150,000 - $180,000 USD. With commission at target, OTE is $240,000.",
            salary_min=150000, salary_max=180000)
        assert a.status == CompensationStatus.CONFIRMED_ABOVE
        assert any("base salary" in e for e in a.basis_evidence)

    def test_9_a_sum_expressed_as_base_plus_incentives_is_not_base(self):
        """Automation Anywhere's real wording: a base label inside an
        additive expression describes the sum, not the base."""
        from careers_os.domain.compensation import CompensationStatus
        a = self._assess(
            "The salary range or on-target earnings (base salary + on-target incentives) for this "
            "position is $165,000 - $180,000 a year.",
            salary_min=165000, salary_max=180000)
        assert a.status == CompensationStatus.BASIS_UNCERTAIN and not a.confirmed

    def test_10_trailing_qualifier_after_the_figure_is_honoured(self):
        """Huntress's real wording puts the qualifier after the range."""
        from careers_os.domain.compensation import CompensationStatus
        a = self._assess("Compensation Range: $130,000-$160,000 base plus bonus and equity.",
                         salary_min=130000, salary_max=160000)
        assert a.status == CompensationStatus.BASIS_UNCERTAIN and not a.confirmed

    def test_11_missing_compensation_is_never_manufactured_into_confirmation(self):
        """Coinbase-style: the employer's own posting publishes no figure."""
        from careers_os.domain.compensation import CompensationStatus
        a = self._assess(
            "Base salary is reviewed annually and benchmarked against market data. We offer equity, "
            "a target bonus, and a generous total rewards package.")
        assert a.status == CompensationStatus.UNKNOWN and not a.confirmed
        assert a.low is None and a.high is None

    def test_generic_salary_label_does_not_override_global_non_base_language(self):
        """"Salary range" is used loosely, so it is not strong enough to
        beat document-level OTE/total-comp framing — only "base" is."""
        from careers_os.domain.compensation import CompensationStatus
        a = self._assess(
            "Salary range $130,000 - $150,000. This reflects total compensation for the role.",
            salary_min=130000, salary_max=150000)
        assert a.status == CompensationStatus.BASIS_UNCERTAIN

    def test_contract_hourly_is_unaffected_by_local_basis_parsing(self):
        from careers_os.domain.compensation import CompensationBasis, CompensationStatus
        from careers_os.domain.enums import EmploymentType
        a = self._assess("Base salary range $130,000 - $160,000 for perm; contract rate applies here.",
                         employment_type=EmploymentType.CONTRACT, hourly_min=95, hourly_max=120)
        assert a.status == CompensationStatus.NON_SALARY and a.basis == CompensationBasis.HOURLY

    def test_k_formatted_base_range_is_recognised(self):
        from careers_os.domain.compensation import CompensationStatus
        a = self._assess("Base salary range $130k-$160k. Total rewards include equity.",
                         salary_min=130000, salary_max=160000)
        assert a.status == CompensationStatus.CONFIRMED_ABOVE and a.confirmed
