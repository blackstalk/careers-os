"""Alert policy (Phase 4).

Deliberately not a competing score. The only input is the existing
pursue-worthiness decision (career/pursue.py) — eligibility, hard
qualification gaps, opportunity cost, and everything else that already
feeds that recommendation is what decides alert-worthiness, not title
keywords, raw eligibility, or compensation alone. See docs/alerts.md.
"""

from careers_os.career.preferences import AlertPolicy
from careers_os.domain.enums import OperatingMode, SearchTrack
from typing import Optional

from careers_os.domain.opportunity_decision import OpportunityDecision, PursueRecommendation, Readiness

# Ordinal rank for alert-threshold comparison only — never used elsewhere.
# `verify_first` and `do_not_pursue` are deliberately excluded from this
# table (see `is_alert_eligible`): a "confirm this first" or "don't
# pursue" outcome is never alert-worthy, in either operating mode,
# regardless of how the threshold is configured.
_ALERT_RANK: dict[PursueRecommendation, int] = {
    PursueRecommendation.LOW_PRIORITY: 1,
    PursueRecommendation.CONSIDER: 2,
    PursueRecommendation.PURSUE: 3,
    PursueRecommendation.STRONG_PURSUE: 4,
}


def is_alert_eligible(decision: OpportunityDecision) -> bool:
    """Hard floor beneath the configurable threshold: an unresolved
    eligibility question or a failed hard qualification gate is never
    alert-worthy, no matter how the policy is configured. This is
    defense-in-depth, not the primary gate — `career/pursue.py` already
    keeps these recommendations out of `pursue`/`strong_pursue` territory.
    """
    return decision.pursue.recommendation in _ALERT_RANK


def should_alert(
    decision: OpportunityDecision,
    policy: AlertPolicy,
    mode: OperatingMode,
    track: SearchTrack | None = None,
) -> bool:
    """Whether this opportunity crosses its track's alert threshold.
    `track=None` keeps the pre-Phase-5 behavior of judging against the
    operating mode's single threshold."""
    if not is_alert_eligible(decision):
        return False
    settings = policy.for_track(track, mode) if track is not None else policy.for_mode(mode)
    threshold = settings.minimum_pursue
    if threshold not in _ALERT_RANK:
        # Defensive: a misconfigured threshold (e.g. someone sets
        # minimum_pursue: verify_first) can never be satisfied — fail
        # closed (no alerts) rather than alerting on everything.
        return False
    return _ALERT_RANK[decision.pursue.recommendation] >= _ALERT_RANK[threshold]


CONFIRMED_LANE = "confirmed"
VERIFICATION_LANE = "verification"


def alert_lane(
    decision: OpportunityDecision, policy: AlertPolicy, mode: OperatingMode, track: SearchTrack
) -> Optional[str]:
    """Which alert lane this opportunity belongs to, or None.

    Opportunities whose pay or eligibility is unconfirmed are judged
    against a separate, lower threshold and a separate budget, so a
    strong replacement with an unpublished salary can still surface
    without ever being presented as a confirmed match (Phase 5).
    """
    if not is_alert_eligible(decision):
        return None
    settings = policy.for_track(track, mode)
    rank = _ALERT_RANK[decision.pursue.recommendation]

    if decision.readiness.level == Readiness.NEEDS_VERIFICATION:
        if settings.max_verification_alerts_per_run <= 0:
            return None
        floor = settings.verification_minimum_pursue
        return VERIFICATION_LANE if floor in _ALERT_RANK and rank >= _ALERT_RANK[floor] else None

    floor = settings.minimum_pursue
    return CONFIRMED_LANE if floor in _ALERT_RANK and rank >= _ALERT_RANK[floor] else None


def alert_rank(recommendation: PursueRecommendation) -> int:
    """Exposed for notification-dedup comparisons (ingestion/scheduled_run.py)
    — a job previously alerted at a lower rank should still be eligible
    for a fresh alert if it later improves, e.g. pursue -> strong_pursue.
    """
    return _ALERT_RANK.get(recommendation, 0)
