from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from careers_os.domain.taxonomy import SkillCategory

EvidenceSourceType = Literal["resume", "portfolio", "github", "manual"]


class EvidenceProvenance(BaseModel):
    """Where a piece of evidence came from. Every Evidence item must have
    one — see docs/evidence-model.md "Provenance is not optional".
    """

    source_type: EvidenceSourceType
    source_name: str  # e.g. a resume variant's display name
    section: Optional[str] = None
    company: Optional[str] = None
    career_role_id: Optional[str] = None
    variant_slug: Optional[str] = None
    extracted_at: datetime


class Evidence(BaseModel):
    """One concrete, provenance-backed statement about the candidate's
    background. Never manufactured — every field here must trace back to
    something an importer actually read, not something inferred because it
    would "commonly accompany" another skill (see docs/evidence-model.md).
    """

    id: str
    type: SkillCategory
    statement: str
    canonical_skills: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    provenance: EvidenceProvenance

    # Populated when the evidence is tied to a dated CareerRole — used by
    # career/timeline.py's years-of-experience calculation. None means
    # "career-wide inventory" evidence with no specific date range (e.g. a
    # skills-table entry not tied to one job) — see docs/evidence-model.md.
    start_date: Optional[date] = None
    end_date: Optional[date] = None
