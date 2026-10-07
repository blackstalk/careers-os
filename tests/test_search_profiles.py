import pytest

from careers_os.career.search_profiles import SearchProfilesConfig
from careers_os.domain.enums import SearchTrack
from careers_os.domain.enums import EmploymentType


class TestSearchProfilesConfig:
    def test_loads_default_profiles(self):
        config = SearchProfilesConfig.load()
        assert set(config.profiles) == {
            "craft", "php", "laravel", "fullstack_js", "backend_api", "wordpress", "fde",
        }

    def test_profiles_are_attributed_to_a_track(self):
        config = SearchProfilesConfig.load()
        replacement = {n for n, p in config.profiles.items() if p.track == SearchTrack.REPLACEMENT}
        exploratory = {n for n, p in config.profiles.items() if p.track == SearchTrack.EXPLORATORY}
        assert replacement == {"craft", "php", "laravel", "fullstack_js", "backend_api", "wordpress"}
        assert exploratory == {"fde"}

    def test_retired_profile_name_confers_no_track(self):
        # Phase 5.1: a stale provenance string pulled LangChain
        # professional-services roles into the replacement lane. A
        # retired name is history, not a current lens.
        config = SearchProfilesConfig.load()
        assert config.track_for("backend_platform") is None
        assert "backend_platform" in config.retired_profiles

    def test_unknown_profile_name_confers_no_track(self):
        assert SearchProfilesConfig.load().track_for("some_future_profile") is None

    def test_tracks_for_uses_only_profiles_that_exist_today(self):
        config = SearchProfilesConfig.load()
        # Retired-only provenance: discoverable, but not replacement.
        assert config.tracks_for(["backend_platform"]) == [SearchTrack.EXPLORATORY]
        # Retired + exploratory: exploratory only.
        assert config.tracks_for(["backend_platform", "fde"]) == [SearchTrack.EXPLORATORY]
        # Genuine multi-track membership is preserved.
        assert config.tracks_for(["fullstack_js", "fde"]) == [
            SearchTrack.EXPLORATORY, SearchTrack.REPLACEMENT]
        assert config.tracks_for([]) == [SearchTrack.EXPLORATORY]

    def test_craft_is_the_highest_priority_replacement_anchor(self):
        config = SearchProfilesConfig.load()
        assert config.profiles["craft"].priority < config.profiles["wordpress"].priority

    def test_each_profile_has_at_least_one_query(self):
        config = SearchProfilesConfig.load()
        for profile in config.profiles.values():
            assert len(profile.queries) >= 1

    def test_default_employment_types_include_full_time(self):
        # Full-time was deliberately added (see search_profiles.yaml's
        # comment and docs/discovery.md#employment-type-policy) — a
        # compelling full-time role should reach the same eligibility ->
        # qualification -> pursue -> alert pipeline as everything else,
        # not be hidden before it ever gets evaluated.
        config = SearchProfilesConfig.load()
        assert EmploymentType.FULL_TIME in config.default_employment_types
        assert EmploymentType.CONTRACT in config.default_employment_types
        assert EmploymentType.FREELANCE in config.default_employment_types
        assert EmploymentType.PART_TIME in config.default_employment_types

    def test_enabled_profiles_returns_only_enabled_by_default(self):
        config = SearchProfilesConfig(
            profiles={
                "a": {"name": "a", "enabled": True, "queries": ["A"]},
                "b": {"name": "b", "enabled": False, "queries": ["B"]},
            }
        )
        names = {p.name for p in config.enabled_profiles()}
        assert names == {"a"}

    def test_explicit_profile_request_overrides_disabled_flag(self):
        config = SearchProfilesConfig(
            profiles={
                "a": {"name": "a", "enabled": True, "queries": ["A"]},
                "b": {"name": "b", "enabled": False, "queries": ["B"]},
            }
        )
        names = {p.name for p in config.enabled_profiles(["b"])}
        assert names == {"b"}

    def test_unknown_profile_name_raises(self):
        config = SearchProfilesConfig.load()
        with pytest.raises(ValueError):
            config.enabled_profiles(["does-not-exist"])
