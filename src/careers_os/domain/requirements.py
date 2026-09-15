from enum import Enum
from typing import Optional

from pydantic import BaseModel

from careers_os.domain.taxonomy import SkillCategory


class RequirementImportance(str, Enum):
    """How strictly a requirement gates qualification (career/eligibility
    is separate — see domain/eligibility.py). Only `HARD_REQUIRED` can
    produce a qualification FAIL on its own; see scoring/qualification.py.
    """

    HARD_REQUIRED = "hard_required"  # explicit minimum (e.g. "3+ years Go") in a required section
    REQUIRED = "required"  # appears in the posting's required/unmarked section, no explicit minimum
    PREFERRED = "preferred"  # appears after a "preferred qualifications"-style marker
    INFORMATIONAL = "informational"  # mentioned but not a qualification signal (reserved for future use)
    UNKNOWN = "unknown"


class JobRequirement(BaseModel):
    """One requirement extracted from a job posting.

    `canonical_skill` is the taxonomy key (career/data/skills_taxonomy.yaml)
    when the requirement matched a known skill; `None` for a
    RESPONSIBILITY-category phrase that didn't map to anything in the
    taxonomy (those are surfaced as `unknown` matches, never guessed at).

    `id` is stable within one extraction run (canonical_skill, or a
    generated key for a skill-less generic-years requirement) — it exists
    so the optional AI evidence reasoner can cite *which* requirement it's
    assessing, and so that citation can be validated (see
    scoring/evidence_reasoner.py).
    """

    id: Optional[str] = None
    category: SkillCategory
    canonical_skill: Optional[str] = None
    text: str
    is_preferred: bool = False  # False = required/unspecified, True = "preferred"/"nice to have"
    importance: RequirementImportance = RequirementImportance.REQUIRED
    raw_context: Optional[str] = None
    min_years: Optional[int] = None
