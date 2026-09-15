from datetime import datetime, timedelta, timezone

from careers_os.career.freshness import assess_freshness
from careers_os.career.preferences import FreshnessThresholds
from careers_os.domain.opportunity_decision import Freshness

NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)


class TestFreshness:
    def test_no_posted_date_is_unknown(self):
        result = assess_freshness(None, as_of=NOW)
        assert result.level == Freshness.UNKNOWN

    def test_within_fresh_window(self):
        result = assess_freshness(NOW - timedelta(days=2), as_of=NOW)
        assert result.level == Freshness.FRESH

    def test_within_recent_window(self):
        result = assess_freshness(NOW - timedelta(days=20), as_of=NOW)
        assert result.level == Freshness.RECENT

    def test_within_aging_window(self):
        result = assess_freshness(NOW - timedelta(days=60), as_of=NOW)
        assert result.level == Freshness.AGING

    def test_beyond_aging_window_is_stale_not_closed(self):
        result = assess_freshness(NOW - timedelta(days=100), as_of=NOW)
        assert result.level == Freshness.STALE
        assert "closure not confirmed" in result.reason.lower()

    def test_custom_thresholds_are_respected(self):
        thresholds = FreshnessThresholds(fresh_days=1, recent_days=2, aging_days=3)
        result = assess_freshness(NOW - timedelta(days=2), thresholds, as_of=NOW)
        assert result.level == Freshness.RECENT
