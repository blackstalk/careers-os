from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

DEFAULT_ACCOUNTS_PATH = Path(__file__).parent / "accounts.yaml"


class WorkableAccountsConfig(BaseModel):
    accounts: list[str]

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "WorkableAccountsConfig":
        path = path or DEFAULT_ACCOUNTS_PATH
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**data)
