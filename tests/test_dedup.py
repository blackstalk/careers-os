from careers_os.storage.dedup import (
    MIN_CONFIDENCE_TO_FLAG,
    compare_jobs,
    normalize_company,
    normalize_title,
)


class TestNormalization:
    def test_title_normalization_is_case_and_punctuation_insensitive(self):
        assert normalize_title("Solutions Architect!") == normalize_title("solutions architect")

    def test_company_normalization_strips_common_suffixes(self):
        assert normalize_company("Acme Inc.") == normalize_company("Acme")
        assert normalize_company("Acme LLC") == normalize_company("Acme")


class TestCompareJobs:
    def test_title_alone_never_flags(self):
        # Same title, different everything else — must never be flagged.
        signal = compare_jobs(
            "Solutions Architect", "Company A", "Austin, TX", "Do backend work.",
            "Solutions Architect", "Company B", "Remote", "Do frontend work.",
        )
        assert signal.confidence < MIN_CONFIDENCE_TO_FLAG

    def test_title_and_company_match_flags_high_confidence(self):
        signal = compare_jobs(
            "Solutions Architect", "Anthropic", "San Francisco, CA", None,
            "Solutions Architect", "Anthropic Inc.", "San Francisco, CA", None,
        )
        assert signal.confidence >= MIN_CONFIDENCE_TO_FLAG
        assert "title" in signal.matched
        assert "company" in signal.matched
        assert "location" in signal.matched

    def test_title_and_company_match_without_location_still_flags_but_lower(self):
        with_loc = compare_jobs(
            "Solutions Architect", "Anthropic", "SF", None,
            "Solutions Architect", "Anthropic", "SF", None,
        )
        without_loc = compare_jobs(
            "Solutions Architect", "Anthropic", "SF", None,
            "Solutions Architect", "Anthropic", "NYC", None,
        )
        assert without_loc.confidence < with_loc.confidence
        assert without_loc.confidence >= MIN_CONFIDENCE_TO_FLAG

    def test_highly_similar_description_alone_can_flag(self):
        text = "We are looking for a solutions architect to own integrations and APIs. " * 3
        signal = compare_jobs(
            "Solutions Architect", "Company A", "Austin", text,
            "Sr. Solutions Architect", "Company B", "Remote", text,
        )
        assert signal.confidence >= MIN_CONFIDENCE_TO_FLAG
        assert "description_similarity" in signal.matched

    def test_dissimilar_everything_never_flags(self):
        signal = compare_jobs(
            "Graphic Designer", "Creative Co", "Miami", "Design social graphics.",
            "Backend Engineer", "Tech Co", "Seattle", "Build distributed systems.",
        )
        assert signal.confidence == 0.0
