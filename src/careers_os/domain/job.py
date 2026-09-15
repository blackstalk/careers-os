from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from careers_os.domain.enums import EmploymentType, JobStatus, RemoteStatus


class NormalizedJob(BaseModel):
    """Canonical job schema every source adapter maps into.

    Nothing downstream of normalization (dedup, scoring, storage, review)
    may know anything about how a particular source represents a job.
    Source-specific detail lives in the paired RawJob, not here.
    """

    id: Optional[int] = None

    source: str
    source_job_id: str
    source_url: str

    title: str
    company: Optional[str] = None
    location: Optional[str] = None
    remote_status: RemoteStatus = RemoteStatus.UNKNOWN

    employment_type: EmploymentType = EmploymentType.UNKNOWN

    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    hourly_min: Optional[float] = None
    hourly_max: Optional[float] = None
    currency: Optional[str] = None

    description: Optional[str] = None

    skills: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)

    posted_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    retrieved_at: datetime

    recruiter_name: Optional[str] = None
    recruiter_contact: Optional[str] = None

    status: JobStatus = JobStatus.NEW

    # Lifecycle bookkeeping (see docs/architecture.md#change-tracking)
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    source_updated_at: Optional[datetime] = None


def format_compensation(
    salary_min: Optional[float], salary_max: Optional[float],
    hourly_min: Optional[float], hourly_max: Optional[float],
) -> str:
    """Shared by cli.py's display and notifications/ alert content — a
    job's compensation is never cross-converted between salary/hourly
    (see docs/sources/creative-circle.md), so this only ever formats
    whichever figure the source actually published.
    """
    parts = []
    if salary_min or salary_max:
        lo = f"${salary_min:,.0f}" if salary_min else "?"
        hi = f"${salary_max:,.0f}" if salary_max else "?"
        parts.append(f"{lo}-{hi}/yr")
    if hourly_min or hourly_max:
        lo = f"${hourly_min:,.0f}" if hourly_min else "?"
        hi = f"${hourly_max:,.0f}" if hourly_max else "?"
        parts.append(f"{lo}-{hi}/hr")
    return ", ".join(parts) if parts else "(not published)"
