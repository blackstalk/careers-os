from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

DEFAULT_PREFERENCES_PATH = Path(__file__).parent / "data" / "preferences.yaml"


class FullTimeComp(BaseModel):
    minimum_annual: float
    strong_annual: float


class ContractComp(BaseModel):
    minimum_hourly: float
    strong_hourly: float
    exceptional_hourly: float


class CompensationPreferences(BaseModel):
    full_time: FullTimeComp
    contract: ContractComp
    missing_compensation_penalty: float = 0.0


class WorkArrangementPreferences(BaseModel):
    preferred: list[str]
    onsite_penalty: float
    hybrid_penalty: float


class ComponentWeights(BaseModel):
    role_fit: float
    technical_fit: float
    career_direction_fit: float
    compensation_fit: float
    work_arrangement_fit: float
    experience_fit: float

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()


class Preferences(BaseModel):
    compensation: CompensationPreferences
    work_arrangement: WorkArrangementPreferences
    component_weights: ComponentWeights

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Preferences":
        path = path or DEFAULT_PREFERENCES_PATH
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**data)
