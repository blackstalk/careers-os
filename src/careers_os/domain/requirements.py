from typing import Optional

from pydantic import BaseModel

from careers_os.domain.taxonomy import SkillCategory


class JobRequirement(BaseModel):
    """One requirement extracted from a job posting.

    `canonical_skill` is the taxonomy key (career/data/skills_taxonomy.yaml)
    when the requirement matched a known skill; `None` for a
    RESPONSIBILITY-category phrase that didn't map to anything in the
    taxonomy (those are surfaced as `unknown` matches, never guessed at).
    """

    category: SkillCategory
    canonical_skill: Optional[str] = None
    text: str
    is_preferred: bool = False  # False = required/unspecified, True = "preferred"/"nice to have"
    raw_context: Optional[str] = None
    min_years: Optional[int] = None
