from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

DEFAULT_COMPANIES_PATH = Path(__file__).parent / "companies.yaml"


class LeverCompaniesConfig(BaseModel):
    companies: list[str]

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "LeverCompaniesConfig":
        path = path or DEFAULT_COMPANIES_PATH
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**data)
