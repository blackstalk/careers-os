"""Deterministic hard-constraint (eligibility) evaluation.

Separate from fit/qualification scoring entirely — see docs/eligibility.md.
No LLM involved: these are the kinds of yes/no facts (timezone, work
authorization, security clearance, relocation, work arrangement) that
should never be left to a language model's judgment when the candidate's
own facts and the job's own wording are available to compare directly.

Deliberately conservative: a constraint is only ever flagged when the
job's text uses one of a small set of well-understood phrasings. Anything
softer is left undetected rather than guessed at.
"""

import re

from careers_os.career.candidate import CandidateProfile
from careers_os.domain.eligibility import (
    ConstraintType,
    EligibilityCheck,
    EligibilityResult,
    EligibilityStatus,
    rollup_status,
)
from careers_os.domain.enums import RemoteStatus
from careers_os.domain.job import NormalizedJob

# Maps a timezone *name* as it commonly appears in job postings to the IANA
# zone it corresponds to, for comparison against candidate.location.timezone.
_TIMEZONE_NAMES = {
    "pacific": "America/Los_Angeles",
    "mountain": "America/Denver",
    "central": "America/Chicago",
    "eastern": "America/New_York",
}

# "located in ... time zone" phrasing is inherently ambiguous (residency vs.
# working-hours overlap) — always VERIFY, never a hard ineligible, per
# docs/eligibility.md. This matches how the requirement is phrased in
# practice, not just the region name.
_TIMEZONE_PHRASE_RE = re.compile(
    r"(located|based|reside|residing)[^.]{0,60}\b(pacific|mountain|central|eastern)\b[^.]{0,20}time ?zones?",
    re.IGNORECASE,
)

# Unambiguous physical residency, stated as "must reside/live in <place>"
# with no timezone framing at all.
_RESIDENCY_RE = re.compile(
    # Case-insensitive only for the literal phrase — the captured place name
    # must stay case-sensitive so the greedy match stops at the first
    # lowercase word (e.g. "Texas for this position" -> just "Texas").
    r"(?i:must (?:reside|live) in (?:the )?)([A-Z][a-zA-Z.]+(?:\s[A-Z][a-zA-Z.]+)*)",
)

_WORK_AUTH_MARKERS = (
    "authorized to work in the united states",
    "authorized to work in the us",
    "without sponsorship",
    "will not sponsor",
    "does not sponsor",
    "no sponsorship",
    "unable to sponsor",
)
_SPONSORSHIP_NEEDED_MARKERS = ("require sponsorship", "requires sponsorship", "need sponsorship")

_CLEARANCE_MARKERS = ("security clearance", "must be able to obtain a clearance", "active clearance")

_RELOCATION_MARKERS = ("relocation required", "must relocate", "requires relocation")


def _check_timezone(text: str, candidate: CandidateProfile) -> list[EligibilityCheck]:
    match = _TIMEZONE_PHRASE_RE.search(text)
    if not match:
        return []
    region = match.group(2).lower()
    required_zone = _TIMEZONE_NAMES[region]
    return [
        EligibilityCheck(
            requirement=match.group(0).strip(),
            constraint_type=ConstraintType.TIMEZONE,
            candidate_evidence=candidate.location.timezone,
            status=EligibilityStatus.VERIFY,
            confidence=0.5,
            reason=(
                f"Job text requires being 'located'/'based' in the {region.title()} time zone "
                f"({required_zone}); candidate is in {candidate.location.timezone}. Time-zone "
                "phrasing is ambiguous between physical residency and working-hours overlap — "
                "verify with the employer rather than assuming either interpretation."
            ),
        )
    ]


def _check_residency(text: str, candidate: CandidateProfile) -> list[EligibilityCheck]:
    match = _RESIDENCY_RE.search(text)
    if not match:
        return []
    required_place = match.group(1).strip()
    candidate_place = candidate.location.state
    if required_place.lower() in (candidate_place.lower(), candidate.location.country.lower(), "the united states", "united states", "us", "usa"):
        status = EligibilityStatus.ELIGIBLE
        reason = f"Job requires residency in {required_place}; candidate's location satisfies this."
    else:
        status = EligibilityStatus.INELIGIBLE
        reason = (
            f"Job explicitly requires residing in {required_place}; candidate resides in "
            f"{candidate_place}, {candidate.location.country}."
        )
    return [
        EligibilityCheck(
            requirement=match.group(0).strip(),
            constraint_type=ConstraintType.GEOGRAPHIC_RESIDENCY,
            candidate_evidence=f"{candidate_place}, {candidate.location.country}",
            status=status,
            confidence=0.85,
            reason=reason,
        )
    ]


def _check_work_authorization(text: str, candidate: CandidateProfile) -> list[EligibilityCheck]:
    text_lower = text.lower()
    if not any(marker in text_lower for marker in _WORK_AUTH_MARKERS):
        return []
    wa = candidate.work_authorization
    if wa.authorized and not wa.sponsorship_required:
        status, reason = (
            EligibilityStatus.ELIGIBLE,
            f"Job requires work authorization without sponsorship; candidate is authorized to "
            f"work in {wa.country} and does not require sponsorship.",
        )
    elif wa.sponsorship_required:
        status, reason = (
            EligibilityStatus.INELIGIBLE,
            "Job requires work authorization without employer sponsorship; candidate profile "
            "indicates sponsorship would be required.",
        )
    else:
        status, reason = (
            EligibilityStatus.UNKNOWN,
            "Job mentions work-authorization requirements but candidate's authorization status "
            "isn't clearly configured.",
        )
    return [
        EligibilityCheck(
            requirement="Authorized to work without employer sponsorship",
            constraint_type=ConstraintType.WORK_AUTHORIZATION,
            candidate_evidence=f"authorized={wa.authorized}, sponsorship_required={wa.sponsorship_required}",
            status=status,
            confidence=0.9,
            reason=reason,
        )
    ]


def _check_security_clearance(text: str, candidate: CandidateProfile) -> list[EligibilityCheck]:
    text_lower = text.lower()
    if not any(marker in text_lower for marker in _CLEARANCE_MARKERS):
        return []
    held = candidate.security_clearance.held
    if held is True:
        status, reason = EligibilityStatus.ELIGIBLE, "Job requires a security clearance; candidate holds one."
    elif held is False:
        status, reason = EligibilityStatus.INELIGIBLE, "Job requires a security clearance; candidate does not hold one."
    else:
        status, reason = (
            EligibilityStatus.UNKNOWN,
            "Job mentions a security clearance requirement; candidate's clearance status isn't configured.",
        )
    return [
        EligibilityCheck(
            requirement="Security clearance",
            constraint_type=ConstraintType.SECURITY_CLEARANCE,
            candidate_evidence=f"held={held}",
            status=status,
            confidence=0.85,
            reason=reason,
        )
    ]


def _check_relocation(text: str, candidate: CandidateProfile) -> list[EligibilityCheck]:
    text_lower = text.lower()
    if not any(marker in text_lower for marker in _RELOCATION_MARKERS):
        return []
    willing = candidate.relocation.willing
    status = EligibilityStatus.ELIGIBLE if willing else EligibilityStatus.INELIGIBLE
    reason = (
        "Job requires relocation; candidate is willing to relocate."
        if willing
        else "Job requires relocation; candidate profile indicates unwillingness to relocate."
    )
    return [
        EligibilityCheck(
            requirement="Relocation required",
            constraint_type=ConstraintType.RELOCATION,
            candidate_evidence=f"willing={willing}",
            status=status,
            confidence=0.8,
            reason=reason,
        )
    ]


def _check_work_arrangement(job: NormalizedJob, candidate: CandidateProfile) -> list[EligibilityCheck]:
    if job.remote_status == RemoteStatus.ONSITE:
        level = candidate.work_preferences.onsite
    elif job.remote_status == RemoteStatus.HYBRID:
        level = candidate.work_preferences.hybrid
    else:
        return []

    if level == "unacceptable":
        status, reason = (
            EligibilityStatus.INELIGIBLE,
            f"Job requires {job.remote_status.value} presence; candidate has marked this arrangement unacceptable.",
        )
    elif level == "acceptable":
        status, reason = (
            EligibilityStatus.ELIGIBLE,
            f"Job requires {job.remote_status.value} presence; candidate has marked this arrangement acceptable.",
        )
    else:  # "preferred"
        status, reason = (
            EligibilityStatus.ELIGIBLE,
            f"Job requires {job.remote_status.value} presence; candidate prefers this arrangement.",
        )
    return [
        EligibilityCheck(
            requirement=f"{job.remote_status.value.title()} presence required",
            constraint_type=ConstraintType.WORK_ARRANGEMENT,
            candidate_evidence=f"{job.remote_status.value}={level}",
            status=status,
            confidence=0.9,
            reason=reason,
        )
    ]


def evaluate_eligibility(job: NormalizedJob, candidate: CandidateProfile) -> EligibilityResult:
    text = f"{job.title}\n{job.description or ''}"

    checks: list[EligibilityCheck] = []
    checks += _check_timezone(text, candidate)
    checks += _check_residency(text, candidate)
    checks += _check_work_authorization(text, candidate)
    checks += _check_security_clearance(text, candidate)
    checks += _check_relocation(text, candidate)
    checks += _check_work_arrangement(job, candidate)

    return EligibilityResult(status=rollup_status(checks), checks=checks)
