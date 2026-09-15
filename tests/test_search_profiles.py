import pytest

from careers_os.career.search_profiles import SearchProfilesConfig
from careers_os.domain.enums import EmploymentType


class TestSearchProfilesConfig:
    def test_loads_default_profiles(self):
        config = SearchProfilesConfig.load()
        assert set(config.profiles) == {"php", "laravel", "craft", "wordpress"}

    def test_each_profile_has_at_least_one_query(self):
        config = SearchProfilesConfig.load()
        for profile in config.profiles.values():
            assert len(profile.queries) >= 1

    def test_default_employment_types_exclude_full_time(self):
        config = SearchProfilesConfig.load()
        assert EmploymentType.FULL_TIME not in config.default_employment_types
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
