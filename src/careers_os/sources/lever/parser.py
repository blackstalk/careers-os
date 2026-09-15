"""Parsing/normalization for Lever's public Postings API payloads.

All Lever-specific field names and heuristics live only in this file
(and constants.py) — see careers_os.sources.base.JobSource for why that
matters.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.job import NormalizedJob
from careers_os.domain.raw_job import RawJob
from careers_os.sources.lever.constants import (
    EMPLOYMENT_TYPE_TEXT_MARKERS,
    SALARY_TEXT_PATTERN,
    WORKPLACE_TYPE_TO_REMOTE_STATUS,
)

logger = logging.getLogger("careers_os.sources.lever")

SOURCE_NAME = "lever"


def make_source_job_id(company: str, lever_posting_id: Any) -> str:
    """Lever posting ids are already globally unique, but the company
    slug is still folded in (`{company}:{id}`) so `fetch_job` knows which
    per-company detail endpoint to hit from a bare id string — same
    reasoning as Greenhouse's board token. See source.py.
    """
    return f"{company}:{lever_posting_id}"


def split_source_job_id(source_job_id: str) -> tuple[str, str]:
    company, _, posting_id = source_job_id.partition(":")
    if not company or not posting_id:
        raise ValueError(f"Malformed Lever source_job_id: {source_job_id!r}")
    return company, posting_id


def _location_string(payload: dict[str, Any]) -> Optional[str]:
    categories = payload.get("categories") or {}
    location = categories.get("location")
    return location.strip() if isinstance(location, str) and location.strip() else None


def detect_remote_status(payload: dict[str, Any]) -> RemoteStatus:
    """`workplaceType` is a structured top-level field on the posting
    (observed values: "remote", "hybrid", "onsite") — see
    docs/sources/lever.md. Falls back to scanning location text, same
    heuristic Greenhouse uses, if the field is absent.
    """
    workplace_type = str(payload.get("workplaceType") or "").strip().lower()
    mapped = WORKPLACE_TYPE_TO_REMOTE_STATUS.get(workplace_type)
    if mapped:
        return RemoteStatus(mapped)

    location_text = (_location_string(payload) or "").lower()
    if "remote" in location_text:
        return RemoteStatus.REMOTE
    return RemoteStatus.UNKNOWN


def detect_employment_type(payload: dict[str, Any]) -> EmploymentType:
    """`categories.commitment` is free text set per-company (observed:
    "Full-time", "Contract", "Internship") — no fixed enum, so this is
    scanned the same way Greenhouse's metadata text is.
    """
    categories = payload.get("categories") or {}
    commitment = str(categories.get("commitment") or "").lower()
    for employment_type, markers in EMPLOYMENT_TYPE_TEXT_MARKERS.items():
        if any(marker in commitment for marker in markers):
            return EmploymentType(employment_type)
    return EmploymentType.UNKNOWN


def extract_salary_from_text(text: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    """Low-confidence, best-effort extraction of a salary range embedded
    in free-text (observed pattern on Palantir's board: "The estimated
    salary range for this position is estimated to be $135,000 -
    $200,000/year"). Never treated as reliably present. See
    docs/sources/lever.md "Known limitations".
    """
    if not text:
        return None, None
    match = re.search(SALARY_TEXT_PATTERN, text, re.IGNORECASE)
    if not match:
        return None, None
    try:
        low = float(match.group(1).replace(",", ""))
        high = float(match.group(2).replace(",", ""))
        return low, high
    except ValueError:
        return None, None


def parse_job_payload_to_raw_job(
    payload: dict[str, Any], company: str, *, retrieved_at: Optional[datetime] = None
) -> RawJob:
    retrieved_at = retrieved_at or datetime.now(timezone.utc)

    lever_id = payload.get("id")
    if lever_id is None:
        raise ValueError("Job payload is missing an 'id' field")

    description = payload.get("descriptionPlain")
    additional = payload.get("additionalPlain")
    # Compensation boilerplate is frequently in `additionalPlain` (a
    # separate "Salary" section) rather than the main description — see
    # docs/sources/lever.md. Both are scanned when extracting salary.
    combined_text_for_salary = " ".join(t for t in (description, additional) if t)

    created_at_ms = payload.get("createdAt")
    posted_date = None
    if isinstance(created_at_ms, (int, float)):
        posted_date = datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc).isoformat()

    return RawJob(
        source=SOURCE_NAME,
        source_job_id=make_source_job_id(company, lever_id),
        source_url=payload.get("hostedUrl") or "",
        raw_title=payload.get("text"),
        raw_location=_location_string(payload),
        raw_description=combined_text_for_salary or description,
        raw_compensation=None,  # extracted from text at normalize time, see normalize_raw_job
        raw_employment_type=None,  # no reliable raw field; computed heuristically at normalize time
        raw_posted_date=posted_date,
        raw_updated_date=None,  # not exposed by this API
        raw_recruiter=None,  # not exposed by this API
        raw_payload={**payload, "_company": company},
        retrieved_at=retrieved_at,
    )


def parse_datetime(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        logger.warning("lever.unparseable_date", extra={"value": value})
        return None


def normalize_raw_job(raw: RawJob) -> NormalizedJob:
    payload = raw.raw_payload
    company = payload.get("_company", "")

    salary_min, salary_max = extract_salary_from_text(raw.raw_description)
    title = (raw.raw_title or "").strip() or "(untitled)"

    return NormalizedJob(
        source=raw.source,
        source_job_id=raw.source_job_id,
        source_url=raw.source_url,
        title=title,
        company=company or None,  # Lever's public API doesn't expose a per-job company display name
        location=raw.raw_location,
        remote_status=detect_remote_status(payload),
        employment_type=detect_employment_type(payload),
        salary_min=salary_min,
        salary_max=salary_max,
        hourly_min=None,
        hourly_max=None,
        currency="USD" if salary_min or salary_max else None,
        description=payload.get("descriptionPlain"),
        skills=[],  # Lever's public API exposes no tags/skills field
        technologies=[],
        posted_at=parse_datetime(raw.raw_posted_date),
        updated_at=None,
        retrieved_at=raw.retrieved_at,
        recruiter_name=None,
        recruiter_contact=None,
    )
