from enum import Enum

from pydantic import BaseModel, Field


class ConstraintType(str, Enum):
    TIMEZONE = "timezone"
    GEOGRAPHIC_RESIDENCY = "geographic_residency"
    WORK_AUTHORIZATION = "work_authorization"
    SECURITY_CLEARANCE = "security_clearance"
    RELOCATION = "relocation"
    WORK_ARRANGEMENT = "work_arrangement"


class EligibilityStatus(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    VERIFY = "verify"
    UNKNOWN = "unknown"


class EligibilityCheck(BaseModel):
    """One detected hard constraint compared against candidate facts.

    `status=VERIFY` means the job's own wording is ambiguous (e.g. a
    timezone requirement that could mean physical residency or just
    working-hour overlap) — deliberately not guessed at, see
    docs/eligibility.md. `status=UNKNOWN` means the wording is clear but
    the candidate fact needed to evaluate it isn't configured.
    """

    requirement: str
    constraint_type: ConstraintType
    candidate_evidence: str
    status: EligibilityStatus
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class EligibilityResult(BaseModel):
    status: EligibilityStatus
    checks: list[EligibilityCheck] = Field(default_factory=list)

    @property
    def blocking_checks(self) -> list[EligibilityCheck]:
        return [c for c in self.checks if c.status == EligibilityStatus.INELIGIBLE]

    @property
    def verify_checks(self) -> list[EligibilityCheck]:
        return [c for c in self.checks if c.status == EligibilityStatus.VERIFY]

    @property
    def unknown_checks(self) -> list[EligibilityCheck]:
        return [c for c in self.checks if c.status == EligibilityStatus.UNKNOWN]


def rollup_status(checks: list[EligibilityCheck]) -> EligibilityStatus:
    """No constraints detected at all -> eligible (most postings have none
    explicit in text). One ineligible check dominates everything else;
    verify beats unknown; unknown only when nothing worse was found.
    """
    if not checks:
        return EligibilityStatus.ELIGIBLE
    if any(c.status == EligibilityStatus.INELIGIBLE for c in checks):
        return EligibilityStatus.INELIGIBLE
    if any(c.status == EligibilityStatus.VERIFY for c in checks):
        return EligibilityStatus.VERIFY
    if any(c.status == EligibilityStatus.UNKNOWN for c in checks):
        return EligibilityStatus.UNKNOWN
    return EligibilityStatus.ELIGIBLE
