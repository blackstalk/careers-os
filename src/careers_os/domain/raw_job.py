from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class RawJob(BaseModel):
    """Source data preserved as close to verbatim as practical.

    Nothing here is normalized or interpreted. The point of keeping this
    separate from the canonical job is so a parser bug can be fixed and
    re-run against `raw_payload` without re-fetching the source.
    """

    source: str
    source_job_id: str
    source_url: str

    raw_title: Optional[str] = None
    raw_location: Optional[str] = None
    raw_description: Optional[str] = None
    raw_compensation: Optional[str] = None
    raw_employment_type: Optional[str] = None
    raw_posted_date: Optional[str] = None
    raw_updated_date: Optional[str] = None
    raw_recruiter: Optional[str] = None

    raw_payload: dict[str, Any]
    retrieved_at: datetime
