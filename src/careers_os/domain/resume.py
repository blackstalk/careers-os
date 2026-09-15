from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class CareerRole(BaseModel):
    """One employer/title/date-range fact — "career truth", shared across
    every resume variant that mentions it. Identity is (company, start_date)
    — see storage/resume_repository.py's upsert logic, which mirrors
    JobRepository's dedup-by-key pattern from Phase 1.
    """

    id: str  # slug, e.g. "riviana-foods-inc-technical-systems-manager-2020-06"
    company: str
    title: str
    location: Optional[str] = None
    start_date: date
    end_date: Optional[date] = None  # None = ongoing ("Present")
    is_current: bool = False


class RoleFraming(BaseModel):
    """How one resume variant presents a given CareerRole. Two variants can
    frame the same role very differently (see docs/resume-model.md) without
    duplicating the underlying CareerRole record.
    """

    career_role_id: str
    variant_slug: str
    bullets: list[str] = Field(default_factory=list)
    section: str  # e.g. "Professional Experience"


class ProjectEvidenceEntry(BaseModel):
    """A "Selected Architecture Work" / "Selected Portfolio Work"-style
    project block. These read like sub-projects of a CareerRole but the
    source resumes don't state the parent employer explicitly, so
    `related_career_role_id` is left None rather than guessed — see
    docs/resume-model.md.
    """

    id: str
    variant_slug: str
    title: str
    role_descriptor: Optional[str] = None
    context_line: Optional[str] = None
    bullets: list[str] = Field(default_factory=list)
    related_career_role_id: Optional[str] = None
    section: str


class ResumeVariant(BaseModel):
    """One imported resume file/positioning strategy. Multiple variants can
    (and are expected to) reference the same CareerRole records via
    RoleFraming — see docs/resume-model.md "career truth vs. resume
    presentation".
    """

    slug: str
    display_name: str
    positioning_title: Optional[str] = None
    summary: Optional[str] = None
    source_file_name: str
    source_file_hash: str
    imported_at: datetime
