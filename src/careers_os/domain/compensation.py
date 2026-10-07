"""Compensation certainty (Phase 5).

`score_compensation_fit` answers "how good is this pay?" as a number.
That is not enough for the replacement objective, which asks a different
and stricter question: **is this job confirmed to pay at least the
configured base-salary floor?**

A published range answers that only when its *minimum* clears the floor.
$125K-$150K is confirmed. $100K-$130K is not: it overlaps the floor and
might be offered at $100K. Treating the maximum as the figure (which is
what the scorer did before this module existed) turned "might" into
"confirmed".

Separately, a posted number is only meaningful if it is base salary.
Sources do not publish a compensation basis, so when the posting's own
text frames the figure as OTE, total compensation, or a package
including bonus or equity, the basis is recorded as uncertain rather
than silently assumed to be base.

Phase 7 narrows *where* that framing is read from. Basis is a property of
a particular number, not of the whole document: a posting can say
"Anticipated Base Salary Range $142,000 - $196,600" and later add "your
base pay is one part of your total compensation package", and the range
is still base salary. Scanning the entire description for phrases like
"total compensation" turned that boilerplate into uncertainty. The basis
is now resolved from the language adjacent to each published figure (see
`scoring/deterministic.py`), with the document-wide scan kept only as the
fallback for figures that carry no label of their own.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class CompensationStatus(str, Enum):
    CONFIRMED_ABOVE = "confirmed_above"        # range minimum clears the floor
    CONFIRMED_AT = "confirmed_at"              # range minimum sits exactly at the floor
    OVERLAPS_THRESHOLD = "overlaps_threshold"  # max clears it, min does not — not a guarantee
    BELOW_THRESHOLD = "below_threshold"        # max is under the floor
    BASIS_UNCERTAIN = "basis_uncertain"        # a figure exists but reads as OTE/total comp
    UNKNOWN = "unknown"                        # nothing published
    NON_SALARY = "non_salary"                  # contract/freelance hourly, judged separately


class CompensationBasis(str, Enum):
    BASE_SALARY = "base_salary"
    HOURLY = "hourly"
    UNCERTAIN = "uncertain"  # OTE / total comp / package language near the figure
    UNKNOWN = "unknown"


# Statuses where the floor is genuinely proven by the posting itself.
CONFIRMED_STATUSES = frozenset({CompensationStatus.CONFIRMED_ABOVE, CompensationStatus.CONFIRMED_AT})
# Statuses that need a human to ask the employer before the job can be
# called a replacement match.
VERIFY_STATUSES = frozenset({
    CompensationStatus.OVERLAPS_THRESHOLD,
    CompensationStatus.BASIS_UNCERTAIN,
    CompensationStatus.UNKNOWN,
})


class CompensationAssessment(BaseModel):
    """Structured answer to "does this clear the base-salary floor?",
    kept separate from the 0-1 compensation *score* used for ranking."""

    status: CompensationStatus
    basis: CompensationBasis = CompensationBasis.UNKNOWN
    floor: Optional[float] = None       # the configured minimum this was judged against
    low: Optional[float] = None         # published range minimum, when there is one
    high: Optional[float] = None
    reason: str = ""
    signals: list[str] = Field(default_factory=list)  # phrases that made the basis uncertain
    # Phase 7: the wording *next to the published range* that established
    # it as base salary, e.g. "anticipated base salary range". Recorded so
    # a confirmation can be audited back to the posting's own language
    # rather than taken on trust.
    basis_evidence: list[str] = Field(default_factory=list)

    @property
    def confirmed(self) -> bool:
        return self.status in CONFIRMED_STATUSES

    @property
    def needs_verification(self) -> bool:
        return self.status in VERIFY_STATUSES
