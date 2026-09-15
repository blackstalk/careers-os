from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

DEFAULT_PROFILE_PATH = Path(__file__).parent / "data" / "profile.yaml"


class CareerProfile(BaseModel):
    target_roles: list[str]
    target_role_keywords: list[str]
    core_strengths: list[str]
    technical_interest_keywords: list[str]
    career_direction_keywords: list[str]
    narrative: str

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "CareerProfile":
        path = path or DEFAULT_PROFILE_PATH
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**data)
