"""Parsing/normalization for Ashby's public Job Postings API payloads.

All Ashby-specific field names and heuristics live only in this file (and
constants.py) — see careers_os.sources.base.JobSource for why that
matters.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.job import NormalizedJob
from careers_os.domain.raw_job import RawJob
from careers_os.sources.ashby.constants import (
    EMPLOYMENT_TYPE_MAP,
    WORKPLACE_TYPE_TO_REMOTE_STATUS,
)

logger = logging.getLogger("careers_os.sources.ashby")

SOURCE_NAME = "ashby"


def make_source_job_id(board_name: str, ashby_job_id: Any) -> str:
    """Ashby's job ids are already globally unique, but the board name is
    still folded in (`{board_name}:{id}`) because there is no per-job
    detail endpoint — `fetch_job` needs to know which board to re-list to
    find this id again. See source.py.
    """
    return f"{board_name}:{ashby_job_id}"


def split_source_job_id(source_job_id: str) -> tuple[str, str]:
    board_name, _, job_id = source_job_id.partition(":")
    if not board_name or not job_id:
        raise ValueError(f"Malformed Ashby source_job_id: {source_job_id!r}")
    return board_name, job_id


def detect_remote_status(payload: dict[str, Any]) -> RemoteStatus:
    """Ashby's `workplaceType` is a structured field (unlike Greenhouse's
    free-text location) but is frequently null even on real, listed jobs
    — `isRemote` is checked as a fallback. See docs/sources/ashby.md.
    """
    workplace_type = str(payload.get("workplaceType") or "").strip().lower()
    mapped = WORKPLACE_TYPE_TO_REMOTE_STATUS.get(workplace_type)
    if mapped:
        return RemoteStatus(mapped)
    if payload.get("isRemote") is True:
        return RemoteStatus.REMOTE
    return RemoteStatus.UNKNOWN


def detect_employment_type(payload: dict[str, Any]) -> EmploymentType:
    """Ashby's `employmentType` is a structured enum-like string — no
    heuristic text scanning needed, unlike Greenhouse.
    """
    raw = str(payload.get("employmentType") or "").strip().lower()
    return EMPLOYMENT_TYPE_MAP.get(raw, EmploymentType.UNKNOWN)


def extract_salary_range(payload: dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    """Ashby exposes genuinely structured compensation — `compensation.
    compensationTiers[].components[]` with numeric `minValue`/`maxValue`
    per component — unlike Greenhouse/Lever, which require regex-scraping
    free-text description HTML. Multiple tiers usually mean multiple
    locations/levels for the same posting; this takes the overall
    min-of-mins and max-of-maxes across every `Salary`-type component as
    the posting's full offered range, rather than guessing which single
    tier is "the" one. See docs/sources/ashby.md.
    """
    compensation = payload.get("compensation") or {}
    mins: list[float] = []
    maxes: list[float] = []
    for tier in compensation.get("compensationTiers") or []:
        for component in tier.get("components") or []:
            if component.get("compensationType") != "Salary":
                continue
            if component.get("minValue") is not None:
                mins.append(float(component["minValue"]))
            if component.get("maxValue") is not None:
                maxes.append(float(component["maxValue"]))
    if not mins and not maxes:
        return None, None
    return (min(mins) if mins else None, max(maxes) if maxes else None)


def _location_string(payload: dict[str, Any]) -> Optional[str]:
    location = payload.get("location")
    if isinstance(location, str) and location.strip():
        return location.strip()
    address = (payload.get("address") or {}).get("postalAddress") or {}
    parts = [address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry")]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def parse_job_payload_to_raw_job(
    payload: dict[str, Any], board_name: str, *, retrieved_at: Optional[datetime] = None
) -> RawJob:
    retrieved_at = retrieved_at or datetime.now(timezone.utc)

    ashby_id = payload.get("id")
    if ashby_id is None:
        raise ValueError("Job payload is missing an 'id' field")

    return RawJob(
        source=SOURCE_NAME,
        source_job_id=make_source_job_id(board_name, ashby_id),
        source_url=payload.get("jobUrl") or "",
        raw_title=payload.get("title"),
        raw_location=_location_string(payload),
        raw_description=payload.get("descriptionPlain"),
        raw_compensation=(payload.get("compensation") or {}).get("compensationTierSummary"),
        raw_employment_type=payload.get("employmentType"),
        raw_posted_date=payload.get("publishedAt"),
        raw_updated_date=None,  # not exposed by this API
        raw_recruiter=None,  # not exposed by this API
        raw_payload={**payload, "_board_name": board_name},
        retrieved_at=retrieved_at,
    )


def parse_datetime(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("ashby.unparseable_date", extra={"value": value})
        return None


def normalize_raw_job(raw: RawJob) -> NormalizedJob:
    payload = raw.raw_payload
    board_name = payload.get("_board_name", "")

    salary_min, salary_max = extract_salary_range(payload)
    title = (raw.raw_title or "").strip() or "(untitled)"

    return NormalizedJob(
        source=raw.source,
        source_job_id=raw.source_job_id,
        source_url=raw.source_url,
        title=title,
        company=board_name or None,  # Ashby's public API doesn't expose a per-job company name field
        location=raw.raw_location,
        remote_status=detect_remote_status(payload),
        employment_type=detect_employment_type(payload),
        salary_min=salary_min,
        salary_max=salary_max,
        hourly_min=None,
        hourly_max=None,
        currency="USD" if salary_min or salary_max else None,
        description=raw.raw_description,
        skills=[],  # Ashby's public API exposes no tags/skills field
        technologies=[],
        posted_at=parse_datetime(raw.raw_posted_date),
        updated_at=None,
        retrieved_at=raw.retrieved_at,
        recruiter_name=None,
        recruiter_contact=None,
    )
