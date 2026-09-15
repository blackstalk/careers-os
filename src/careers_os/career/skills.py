from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

from careers_os.domain.taxonomy import SkillCategory

DEFAULT_TAXONOMY_PATH = Path(__file__).parent / "data" / "skills_taxonomy.yaml"


class SkillDefinition(BaseModel):
    key: str
    display_name: str
    aliases: list[str]
    category: SkillCategory
    adjacent: list[str] = []


class SkillsTaxonomy(BaseModel):
    skills: dict[str, SkillDefinition]

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "SkillsTaxonomy":
        path = path or DEFAULT_TAXONOMY_PATH
        with open(path) as f:
            raw = yaml.safe_load(f)
        skills = {
            key: SkillDefinition(key=key, **definition) for key, definition in raw.items()
        }
        return cls(skills=skills)

    def find_in_text(self, text: str) -> list[str]:
        """Return canonical skill keys whose aliases appear in `text`
        (case-insensitive substring match). Order follows taxonomy file
        order, not appearance order in the text.
        """
        lowered = f" {text.lower()} "
        return [
            key
            for key, definition in self.skills.items()
            if any(alias.lower() in lowered for alias in definition.aliases)
        ]

    def adjacent_to(self, key: str) -> list[str]:
        definition = self.skills.get(key)
        return definition.adjacent if definition else []

    def display_name(self, key: str) -> str:
        definition = self.skills.get(key)
        return definition.display_name if definition else key
