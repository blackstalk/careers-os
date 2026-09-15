"""Parsing/normalization for Creative Circle's search and detail JSON.

The search endpoint (`/ccv5-jobs/search`) and detail endpoint
(`/ccv5-jobs/details`) return the *same conceptual fields* under different
casing and, in a few cases, different shapes (e.g. `City`/`StateCode` as
parallel lists on search results vs. a single `citySplit`/`stateCodeSplit`
pair on detail). `_field` below hides that so the rest of this module can
stay declarative.

All Creative Circle-specific field names live only in this file (and
constants.py) — see careers_os.sources.base.JobSource for why that matters.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.job import NormalizedJob
from careers_os.domain.raw_job import RawJob
from careers_os.sources.creative_circle.constants import (
    BASE_URL,
    TAX_TERM_TO_EMPLOYMENT_TYPE,
    WORK_LOCATION_TYPE_TO_REMOTE_STATUS,
)

logger = logging.getLogger("careers_os.sources.creative_circle")

SOURCE_NAME = "creative_circle"

# Ordered (search_key, detail_key) pairs — first non-null wins.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("Id", "id"),
    "job_id": ("jobId", "jobId"),
    "title": ("jobTitle", "jobTitle"),
    "job_url_slug": ("JobURL", "jobURL"),
    "salary_type": ("SalaryType", "salaryType"),
    "date_posted": ("DatePost", "dateCreated"),
    "date_modified": (None, "dateModified"),
    "tax_term": ("TaxTerm", "taxTerm"),
    "recruiter_name": ("RecruiterName", "recruiterName"),
    "recruiter_email": (None, "recruiterEmail"),
    "company_name": ("CompanyName", None),  # not present on detail responses
    "work_location_type_id": ("WorkLocationTypeId", "workLocationTypeId"),
    "salary_min": ("SalaryMin", "salaryMin"),
    "salary_max": ("SalaryMax", "salaryMax"),
    "hourly_min": ("HourlyMin", "hourlyMin"),
    "hourly_max": ("HourlyMax", "hourlyMax"),
    "short_description": ("ShortDescription", None),
    "description_html": (None, "description"),
    "description_text": (None, "descriptionWithoutHtml"),
}


def _field(payload: dict[str, Any], name: str) -> Any:
    for key in _FIELD_ALIASES[name]:
        if key is not None and payload.get(key) not in (None, ""):
            return payload[key]
    return None


def _location_string(payload: dict[str, Any]) -> Optional[str]:
    # Search results: parallel lists, e.g. City=["Wilton"], StateCode=["CT"].
    cities = payload.get("City")
    states = payload.get("StateCode")
    if isinstance(cities, list) and isinstance(states, list) and cities and states:
        parts = [
            f"{city}, {state}" for city, state in zip(cities, states) if city or state
        ]
        if parts:
            return "; ".join(parts)

    # Detail responses: "City,ST" (single pair).
    exact = payload.get("locationsExactSplit")
    if isinstance(exact, str) and exact.strip():
        city, _, state = exact.partition(",")
        city, state = city.strip(), state.strip()
        if city and state:
            return f"{city}, {state}"
        return city or state or None

    return None


def _tags(payload: dict[str, Any]) -> list[str]:
    tags = payload.get("tags")
    if isinstance(tags, list) and tags:
        return [t for t in tags if t]
    split = payload.get("tagsSplit")
    if isinstance(split, str):
        return [t.strip() for t in split.split("`") if t.strip()]
    return []


def parse_datetime(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        # API dates are ISO 8601 with a trailing "Z".
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("creative_circle.unparseable_date", extra={"value": value})
        return None


def parse_job_payload_to_raw_job(
    payload: dict[str, Any], *, retrieved_at: Optional[datetime] = None
) -> RawJob:
    """Build a RawJob from either a search-result item or a detail response.

    Kept source-agnostic-clean deliberately: this returns a `RawJob`, not a
    `NormalizedJob` — interpretation happens in `normalize_raw_job` below.
    """
    retrieved_at = retrieved_at or datetime.now(timezone.utc)

    source_id = _field(payload, "id")
    if source_id is None:
        raise ValueError("Job payload is missing an 'Id'/'id' field")
    source_id = str(source_id)

    location = _location_string(payload)
    hourly_min = _field(payload, "hourly_min")
    hourly_max = _field(payload, "hourly_max")
    salary_min = _field(payload, "salary_min")
    salary_max = _field(payload, "salary_max")
    comp_parts = []
    if salary_min or salary_max:
        comp_parts.append(f"salary {salary_min or '?'}-{salary_max or '?'}")
    if hourly_min or hourly_max:
        comp_parts.append(f"hourly {hourly_min or '?'}-{hourly_max or '?'}")
    raw_compensation = "; ".join(comp_parts) or None

    description = _field(payload, "description_text") or _field(
        payload, "short_description"
    )

    return RawJob(
        source=SOURCE_NAME,
        source_job_id=source_id,
        source_url=f"{BASE_URL}/job-detail/{source_id}",
        raw_title=_field(payload, "title"),
        raw_location=location,
        raw_description=description,
        raw_compensation=raw_compensation,
        raw_employment_type=_field(payload, "tax_term"),
        raw_posted_date=_field(payload, "date_posted"),
        raw_updated_date=_field(payload, "date_modified"),
        raw_recruiter=_field(payload, "recruiter_name"),
        raw_payload=payload,
        retrieved_at=retrieved_at,
    )


def normalize_raw_job(raw: RawJob) -> NormalizedJob:
    """Map a Creative Circle RawJob into the canonical schema.

    Deliberately conservative about compensation: we surface exactly the
    fields the source published (salary and/or hourly, or neither) and
    never derive one from the other.
    """
    payload = raw.raw_payload

    work_location_type_id = _field(payload, "work_location_type_id")
    remote_status = WORK_LOCATION_TYPE_TO_REMOTE_STATUS.get(
        work_location_type_id, RemoteStatus.UNKNOWN
    )

    tax_term = _field(payload, "tax_term")
    employment_type = TAX_TERM_TO_EMPLOYMENT_TYPE.get(tax_term, EmploymentType.UNKNOWN)

    salary_min = _field(payload, "salary_min")
    salary_max = _field(payload, "salary_max")
    hourly_min = _field(payload, "hourly_min")
    hourly_max = _field(payload, "hourly_max")
    has_compensation = any(
        v is not None for v in (salary_min, salary_max, hourly_min, hourly_max)
    )

    title = (raw.raw_title or "").strip() or "(untitled)"

    return NormalizedJob(
        source=raw.source,
        source_job_id=raw.source_job_id,
        source_url=raw.source_url,
        title=title,
        company=_field(payload, "company_name"),
        location=raw.raw_location,
        remote_status=remote_status,
        employment_type=employment_type,
        salary_min=float(salary_min) if salary_min is not None else None,
        salary_max=float(salary_max) if salary_max is not None else None,
        hourly_min=float(hourly_min) if hourly_min is not None else None,
        hourly_max=float(hourly_max) if hourly_max is not None else None,
        currency="USD" if has_compensation else None,
        description=raw.raw_description,
        skills=_tags(payload),
        technologies=[],
        posted_at=parse_datetime(raw.raw_posted_date),
        updated_at=parse_datetime(raw.raw_updated_date),
        retrieved_at=raw.retrieved_at,
        recruiter_name=raw.raw_recruiter,
        recruiter_contact=_field(payload, "recruiter_email"),
    )
