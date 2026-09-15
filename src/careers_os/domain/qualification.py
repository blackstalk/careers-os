from enum import Enum

from pydantic import BaseModel, Field

from careers_os.domain.matching import RequirementMatch


class QualificationStatus(str, Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    FAIL = "fail"
    UNKNOWN = "unknown"


class QualificationResult(BaseModel):
    """Distinct from eligibility (domain/eligibility.py) — a candidate can
    be perfectly eligible (allowed to apply) and still fail qualification
    (unable to credibly do the job), or vice versa. See
    scoring/qualification.py for how `status` is derived; `hard_gaps`
    alone is what should dominate a pursue recommendation (a preferred
    qualification gap never behaves like a hard gate).
    """

    status: QualificationStatus
    hard_gaps: list[RequirementMatch] = Field(default_factory=list)
    reason: str
