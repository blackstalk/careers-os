from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

DEFAULT_CANDIDATE_PATH = Path(__file__).parent / "data" / "candidate.yaml"

WorkPreferenceLevel = str  # "preferred" | "acceptable" | "unacceptable"


class CandidateLocation(BaseModel):
    city: str
    state: str
    country: str
    timezone: str


class WorkAuthorization(BaseModel):
    country: str
    authorized: bool
    sponsorship_required: bool


class WorkPreferences(BaseModel):
    remote: WorkPreferenceLevel = "preferred"
    hybrid: WorkPreferenceLevel = "acceptable"
    onsite: WorkPreferenceLevel = "acceptable"


class Relocation(BaseModel):
    willing: bool = False


class SecurityClearance(BaseModel):
    held: Optional[bool] = None


class CandidateProfile(BaseModel):
    """Structured facts about the candidate for deterministic eligibility
    checks (career/eligibility.py) — see career/data/candidate.yaml.

    Deliberately separate from CareerProfile (positioning/direction) and
    Preferences (scoring weights/penalties): this is facts, not opinion.
    """

    location: CandidateLocation
    work_authorization: WorkAuthorization
    work_preferences: WorkPreferences = WorkPreferences()
    relocation: Relocation = Relocation()
    security_clearance: SecurityClearance = SecurityClearance()

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "CandidateProfile":
        path = path or DEFAULT_CANDIDATE_PATH
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**data)
