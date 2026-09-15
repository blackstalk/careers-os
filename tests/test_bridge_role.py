from careers_os.career.bridge_role import BridgeClassification, classify_bridge_role


class TestBridgeRoleClassification:
    def test_strong_when_anchor_plus_three_bridge_categories(self):
        result = classify_bridge_role(
            "Lead Laravel Platform Engineer",
            "Own the architecture for our Laravel platform, including AWS infrastructure, "
            "REST API integrations, and lead a small customer-facing solutions team.",
        )
        assert result.classification == BridgeClassification.STRONG

    def test_moderate_when_anchor_plus_one_bridge_category(self):
        result = classify_bridge_role(
            "Senior Craft CMS Developer",
            "Build and maintain Craft CMS sites in PHP, working with integrations and APIs "
            "for clients.",
        )
        assert result.classification == BridgeClassification.MODERATE

    def test_weak_when_anchor_only_no_bridge_signal(self):
        result = classify_bridge_role(
            "WordPress Developer",
            "We need someone to handle content updates, basic theme modifications, and "
            "landing page production for our marketing site using WordPress and PHP.",
        )
        assert result.classification == BridgeClassification.WEAK
        assert result.commodity_signals

    def test_none_when_no_anchor_technology_present(self):
        result = classify_bridge_role("Graphic Designer", "Design social media graphics for clients.")
        assert result.classification == BridgeClassification.NONE

    def test_unknown_when_description_too_short_to_judge(self):
        result = classify_bridge_role("Developer", "Short.")
        assert result.classification == BridgeClassification.UNKNOWN

    def test_conventional_development_is_not_automatically_rejected(self):
        # Weak/None are still valid, visible classifications — the function
        # itself never raises or excludes; only ranking (elsewhere) reflects it.
        result = classify_bridge_role(
            "PHP Developer", "Maintain and extend a legacy PHP WordPress site for a small business client."
        )
        assert result.classification in (BridgeClassification.WEAK, BridgeClassification.MODERATE)

    def test_repeated_mentions_of_anchor_tech_do_not_inflate_classification(self):
        # "Avoid keyword dominance": PHP mentioned many times with no
        # forward-direction signal must still classify as weak, not strong.
        repeated = "PHP PHP PHP. We use PHP for everything. PHP developers write PHP code in PHP."
        result = classify_bridge_role("PHP Developer", repeated)
        assert result.classification == BridgeClassification.WEAK

    def test_category_diversity_beats_raw_mention_count(self):
        # Fewer total keyword mentions, but touching 3 distinct bridge
        # categories, must outrank many mentions of the anchor tech alone.
        diverse = classify_bridge_role(
            "PHP Engineer",
            "Use PHP. Own system architecture, deploy to AWS, and lead the team.",
        )
        repeated = classify_bridge_role(
            "PHP Engineer",
            "PHP PHP PHP PHP PHP PHP PHP PHP PHP PHP.",
        )
        order = {
            BridgeClassification.STRONG: 3,
            BridgeClassification.MODERATE: 2,
            BridgeClassification.WEAK: 1,
            BridgeClassification.NONE: 0,
            BridgeClassification.UNKNOWN: -1,
        }
        assert order[diverse.classification] > order[repeated.classification]

    def test_signals_report_distinct_categories_not_keyword_counts(self):
        result = classify_bridge_role(
            "Lead Laravel Platform Engineer",
            "Own the architecture for our Laravel platform, including AWS infrastructure, "
            "REST API integrations, and lead a small customer-facing solutions team.",
        )
        assert len(result.signals) == len(set(result.signals))
