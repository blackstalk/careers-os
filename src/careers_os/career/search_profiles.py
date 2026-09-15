from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

from careers_os.domain.enums import EmploymentType

DEFAULT_SEARCH_PROFILES_PATH = Path(__file__).parent / "data" / "search_profiles.yaml"


class SearchProfile(BaseModel):
    name: str
    enabled: bool = True
    queries: list[str]


class SearchProfilesConfig(BaseModel):
    profiles: dict[str, SearchProfile]
    default_employment_types: list[EmploymentType] = []

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "SearchProfilesConfig":
        path = path or DEFAULT_SEARCH_PROFILES_PATH
        with open(path) as f:
            raw = yaml.safe_load(f)
        profiles = {
            name: SearchProfile(name=name, **definition)
            for name, definition in raw.get("profiles", {}).items()
        }
        return cls(
            profiles=profiles,
            default_employment_types=raw.get("default_employment_types", []),
        )

    def enabled_profiles(self, names: Optional[list[str]] = None) -> list[SearchProfile]:
        """Profiles to actually run: all enabled ones, or a specific
        requested subset (which may include a disabled profile explicitly
        named — an explicit request overrides `enabled: false`).
        """
        if names:
            missing = [n for n in names if n not in self.profiles]
            if missing:
                raise ValueError(f"Unknown search profile(s): {', '.join(missing)}")
            return [self.profiles[n] for n in names]
        return [p for p in self.profiles.values() if p.enabled]
