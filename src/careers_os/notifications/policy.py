"""Alert policy (Phase 4).

Deliberately not a competing score. The only input is the existing
pursue-worthiness decision (career/pursue.py) — eligibility, hard
qualification gaps, opportunity cost, and everything else that already
feeds that recommendation is what decides alert-worthiness, not title
keywords, raw eligibility, or compensation alone. See docs/alerts.md.
"""

from careers_os.career.preferences import AlertPolicy
from careers_os.domain.enums import OperatingMode
from careers_os.domain.opportunity_decision import OpportunityDecision, PursueRecommendation

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


def should_alert(decision: OpportunityDecision, policy: AlertPolicy, mode: OperatingMode) -> bool:
    if not is_alert_eligible(decision):
        return False
    threshold = policy.for_mode(mode).minimum_pursue
    if threshold not in _ALERT_RANK:
        # Defensive: a misconfigured threshold (e.g. someone sets
        # minimum_pursue: verify_first) can never be satisfied — fail
        # closed (no alerts) rather than alerting on everything.
        return False
    return _ALERT_RANK[decision.pursue.recommendation] >= _ALERT_RANK[threshold]


def alert_rank(recommendation: PursueRecommendation) -> int:
    """Exposed for notification-dedup comparisons (ingestion/scheduled_run.py)
    — a job previously alerted at a lower rank should still be eligible
    for a fresh alert if it later improves, e.g. pursue -> strong_pursue.
    """
    return _ALERT_RANK.get(recommendation, 0)
