"""Parsing/normalization for Himalayas' remote-jobs API payloads."""

import html
import re
from datetime import datetime, timezone
from typing import Any, Optional

from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.job import NormalizedJob
from careers_os.domain.raw_job import RawJob
from careers_os.sources.himalayas.constants import EMPLOYMENT_TYPE_MAP

SOURCE_NAME = "himalayas"

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def source_job_id(payload: dict[str, Any]) -> str:
    """The last path segment of `guid` (a stable job URL) is unique across
    Himalayas, e.g. ".../companies/acme/jobs/backend-engineer-123"."""
    guid = payload.get("guid") or payload.get("applicationLink")
    if not guid:
        raise ValueError("Job payload has no 'guid'")
    company, _, slug = str(guid).rstrip("/").partition("/companies/")[2].partition("/jobs/")
    if not company or not slug:
        raise ValueError(f"Unexpected Himalayas guid shape: {guid!r}")
    return f"{company}:{slug}"


def html_to_text(raw_html: Optional[str]) -> Optional[str]:
    if not raw_html:
        return None
    text = _TAG_RE.sub(" ", raw_html)
    text = html.unescape(text)
    return _WHITESPACE_RE.sub(" ", text).strip() or None


def location_string(payload: dict[str, Any]) -> str:
    """Every Himalayas job is remote. `locationRestrictions` lists the
    countries applicants must live in; empty means worldwide. The string
    follows the "<countries> - Remote" pattern the existing remote-location
    eligibility check already understands."""
    countries = payload.get("locationRestrictions") or []
    if not countries:
        return "Remote (Worldwide)"
    return f"{', '.join(countries)} - Remote"


def detect_employment_type(payload: dict[str, Any]) -> EmploymentType:
    raw = str(payload.get("employmentType") or "").strip().lower()
    return EmploymentType(EMPLOYMENT_TYPE_MAP.get(raw, "unknown"))


def parse_pub_date(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_job_payload_to_raw_job(payload: dict[str, Any], *, retrieved_at: Optional[datetime] = None) -> RawJob:
    retrieved_at = retrieved_at or datetime.now(timezone.utc)
    posted = parse_pub_date(payload.get("pubDate"))
    salary = None
    if payload.get("minSalary") or payload.get("maxSalary"):
        salary = f"{payload.get('minSalary')}-{payload.get('maxSalary')} {payload.get('currency') or ''} {payload.get('salaryPeriod') or ''}".strip()
    return RawJob(
        source=SOURCE_NAME,
        source_job_id=source_job_id(payload),
        source_url=payload.get("applicationLink") or payload.get("guid") or "",
        raw_title=payload.get("title"),
        raw_location=location_string(payload),
        raw_description=html_to_text(payload.get("description")),
        raw_compensation=salary,
        raw_employment_type=payload.get("employmentType"),
        raw_posted_date=posted.isoformat() if posted else None,
        raw_updated_date=None,
        raw_recruiter=None,
        raw_payload=payload,
        retrieved_at=retrieved_at,
    )


def normalize_raw_job(raw: RawJob) -> NormalizedJob:
    payload = raw.raw_payload
    annual = str(payload.get("salaryPeriod") or "").lower() in ("annual", "year", "yearly", "")
    hourly = str(payload.get("salaryPeriod") or "").lower() in ("hourly", "hour")
    low, high = _number(payload.get("minSalary")), _number(payload.get("maxSalary"))
    usd = str(payload.get("currency") or "USD").upper() == "USD"
    return NormalizedJob(
        source=raw.source,
        source_job_id=raw.source_job_id,
        source_url=raw.source_url,
        title=(raw.raw_title or "").strip() or "(untitled)",
        company=payload.get("companyName") or None,
        location=raw.raw_location,
        remote_status=RemoteStatus.REMOTE,
        employment_type=detect_employment_type(payload),
        salary_min=low if usd and annual and not hourly else None,
        salary_max=high if usd and annual and not hourly else None,
        hourly_min=low if usd and hourly else None,
        hourly_max=high if usd and hourly else None,
        currency="USD" if usd and (low or high) else None,
        description=raw.raw_description,
        skills=[],
        technologies=[],
        posted_at=datetime.fromisoformat(raw.raw_posted_date) if raw.raw_posted_date else None,
        updated_at=None,
        retrieved_at=raw.retrieved_at,
        recruiter_name=None,
        recruiter_contact=None,
    )
