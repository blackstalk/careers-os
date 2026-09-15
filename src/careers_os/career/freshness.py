"""Job-freshness assessment (Phase 3).

Deliberately does not declare a job closed just because it's old — a
stale posting is still visible at the source, and only a direct
job-detail confirmation (not attempted here — see docs/architecture.md's
change-tracking section) should ever claim closure.
"""

from datetime import datetime, timezone
from typing import Optional

from careers_os.career.preferences import FreshnessThresholds
from careers_os.domain.opportunity_decision import Freshness, FreshnessResult


def assess_freshness(
    posted_at: Optional[datetime],
    thresholds: Optional[FreshnessThresholds] = None,
    as_of: Optional[datetime] = None,
) -> FreshnessResult:
    thresholds = thresholds or FreshnessThresholds()
    if posted_at is None:
        return FreshnessResult(level=Freshness.UNKNOWN, age_days=None, reason="No posted date available.")

    as_of = as_of or datetime.now(timezone.utc)
    # SQLite doesn't reliably round-trip timezone-aware datetimes (a value
    # stored as UTC can come back naive) — treat a naive posted_at as UTC
    # rather than letting the subtraction below raise.
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    age_days = (as_of - posted_at).days

    if age_days <= thresholds.fresh_days:
        return FreshnessResult(level=Freshness.FRESH, age_days=age_days, reason=f"Posted {age_days} day(s) ago.")
    if age_days <= thresholds.recent_days:
        return FreshnessResult(level=Freshness.RECENT, age_days=age_days, reason=f"Posted {age_days} days ago.")
    if age_days <= thresholds.aging_days:
        return FreshnessResult(
            level=Freshness.AGING, age_days=age_days,
            reason=f"Posted {age_days} days ago — getting old but not yet stale.",
        )
    return FreshnessResult(
        level=Freshness.STALE, age_days=age_days,
        reason=f"Posting is {age_days} days old. Still visible at the source; closure not confirmed.",
    )
