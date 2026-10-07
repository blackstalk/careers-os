from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

from careers_os.domain.enums import EmploymentType, SearchTrack

DEFAULT_SEARCH_PROFILES_PATH = Path(__file__).parent / "data" / "search_profiles.yaml"


class SearchProfile(BaseModel):
    """One discovery lens. `track` says which objective it serves;
    `priority` orders the replacement anchors (1 = highest) and is used
    only as a deterministic tie-break between otherwise equally ranked
    opportunities — never as a score, so a strong role from a lower
    priority profile still outranks a mediocre one from a higher priority
    profile.
    """

    name: str
    enabled: bool = True
    queries: list[str]
    track: SearchTrack = SearchTrack.REPLACEMENT
    priority: int = 5


class SearchProfilesConfig(BaseModel):
    profiles: dict[str, SearchProfile]
    default_employment_types: list[EmploymentType] = []
    # Profile names that existed in earlier runs and are still recorded
    # on stored jobs. They are known history, not current lenses: they
    # confer no track, so a job only joins a track through a profile that
    # is actually configured today (Phase 5.1).
    retired_profiles: list[str] = []

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
            retired_profiles=raw.get("retired_profiles", []),
        )

    def track_for(self, profile_name: str) -> Optional[SearchTrack]:
        """The track a profile name belongs to, or None for a retired or
        unknown name.

        Returning None is deliberate (Phase 5.1). Defaulting unknown
        names to `replacement` let a retired profile string recorded on
        an old job pull that job into the primary lane: five LangChain
        professional-services roles reached replacement alerts purely
        through a stale `backend_platform` provenance string. Membership
        now requires a profile that exists today.
        """
        profile = self.profiles.get(profile_name)
        return profile.track if profile else None

    def tracks_for(self, profile_names: list[str]) -> list[SearchTrack]:
        """Every track a job belongs to, from the profiles that actually
        found it. Multi-track membership is legitimate: a role found by
        both `fullstack_js` and `fde` participates in both, and must
        satisfy each track's own rules to alert there. A job with only
        retired/unknown provenance falls back to `exploratory` — still
        discoverable, never able to consume a replacement slot."""
        tracks = {t for t in (self.track_for(n) for n in profile_names) if t is not None}
        return sorted(tracks or {SearchTrack.EXPLORATORY}, key=lambda t: t.value)

    def priority_for(self, profile_name: str) -> int:
        profile = self.profiles.get(profile_name)
        return profile.priority if profile else 99

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
