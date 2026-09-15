from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

DEFAULT_BOARDS_PATH = Path(__file__).parent / "boards.yaml"


class GreenhouseBoardsConfig(BaseModel):
    boards: list[str]

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "GreenhouseBoardsConfig":
        path = path or DEFAULT_BOARDS_PATH
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**data)
