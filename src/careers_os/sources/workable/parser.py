"""Parsing/normalization for Workable's public job-widget API payloads.

All Workable-specific field names and heuristics live only in this file
(and constants.py) — see careers_os.sources.base.JobSource.
"""

import html
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.job import NormalizedJob
from careers_os.domain.raw_job import RawJob
from careers_os.sources.workable.constants import (
    EMPLOYMENT_TYPE_TEXT_MARKERS,
    SALARY_TEXT_PATTERN,
)

logger = logging.getLogger("careers_os.sources.workable")

SOURCE_NAME = "workable"

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def make_source_job_id(account: str, shortcode: Any) -> str:
    """The account slug is folded in (`{account}:{shortcode}`) because
    `fetch_job` has to re-list that account to find the job again — the
    widget API has no per-job detail endpoint (verified: 404).
    """
    return f"{account}:{shortcode}"


def split_source_job_id(source_job_id: str) -> tuple[str, str]:
    account, _, shortcode = source_job_id.partition(":")
    if not account or not shortcode:
        raise ValueError(f"Malformed Workable source_job_id: {source_job_id!r}")
    return account, shortcode


def html_to_text(raw_html: Optional[str]) -> Optional[str]:
    if not raw_html:
        return None
    text = _TAG_RE.sub(" ", raw_html)
    text = html.unescape(text)
    return _WHITESPACE_RE.sub(" ", text).strip() or None


def detect_remote_status(payload: dict[str, Any]) -> RemoteStatus:
    """`telecommuting: true` is an explicit remote flag. `false` only means
    "not marked remote" — it doesn't distinguish hybrid from onsite, so
    it maps to UNKNOWN rather than guessing either.
    """
    if payload.get("telecommuting") is True:
        return RemoteStatus.REMOTE
    return RemoteStatus.UNKNOWN


def detect_employment_type(payload: dict[str, Any]) -> EmploymentType:
    text = str(payload.get("employment_type") or "").lower()
    for employment_type, markers in EMPLOYMENT_TYPE_TEXT_MARKERS.items():
        if any(marker in text for marker in markers):
            return EmploymentType(employment_type)
    return EmploymentType.UNKNOWN


def location_string(payload: dict[str, Any]) -> Optional[str]:
    """City/state/country joined, with "(Remote)" appended for
    telecommuting postings — so a remote role scoped to a country (e.g.
    "Paris, Île-de-France, France (Remote)") reaches the existing
    location-geography eligibility check the same way Ashby/Lever's
    "India - Remote" style strings do.
    """
    parts = [payload.get("city"), payload.get("state"), payload.get("country")]
    base = ", ".join(p for p in parts if p)
    if payload.get("telecommuting") is True:
        return f"{base} (Remote)" if base else "Remote"
    return base or None


def extract_salary_from_text(text: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    if not text:
        return None, None
    match = re.search(SALARY_TEXT_PATTERN, text, re.IGNORECASE)
    if not match:
        return None, None
    try:
        return float(match.group(1).replace(",", "")), float(match.group(2).replace(",", ""))
    except ValueError:
        return None, None


def parse_job_payload_to_raw_job(
    payload: dict[str, Any],
    account: str,
    company_name: Optional[str] = None,
    *,
    retrieved_at: Optional[datetime] = None,
) -> RawJob:
    retrieved_at = retrieved_at or datetime.now(timezone.utc)

    shortcode = payload.get("shortcode")
    if not shortcode:
        raise ValueError("Job payload is missing a 'shortcode' field")

    return RawJob(
        source=SOURCE_NAME,
        source_job_id=make_source_job_id(account, shortcode),
        source_url=payload.get("url") or payload.get("shortlink") or "",
        raw_title=payload.get("title"),
        raw_location=location_string(payload),
        raw_description=html_to_text(payload.get("description")),
        raw_compensation=None,  # no structured field; extracted from text at normalize time
        raw_employment_type=payload.get("employment_type"),
        raw_posted_date=payload.get("published_on") or payload.get("created_at"),
        raw_updated_date=None,
        raw_recruiter=None,
        raw_payload={**payload, "_account": account, "_company_name": company_name},
        retrieved_at=retrieved_at,
    )


def parse_datetime(value: Any) -> Optional[datetime]:
    """Workable dates are plain `YYYY-MM-DD` — treated as midnight UTC so
    they compare cleanly against timezone-aware cutoffs.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        logger.warning("workable.unparseable_date", extra={"value": value})
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def normalize_raw_job(raw: RawJob) -> NormalizedJob:
    payload = raw.raw_payload
    salary_min, salary_max = extract_salary_from_text(raw.raw_description)

    return NormalizedJob(
        source=raw.source,
        source_job_id=raw.source_job_id,
        source_url=raw.source_url,
        title=(raw.raw_title or "").strip() or "(untitled)",
        company=payload.get("_company_name") or payload.get("_account") or None,
        location=raw.raw_location,
        remote_status=detect_remote_status(payload),
        employment_type=detect_employment_type(payload),
        salary_min=salary_min,
        salary_max=salary_max,
        hourly_min=None,
        hourly_max=None,
        currency="USD" if salary_min or salary_max else None,
        description=raw.raw_description,
        skills=[],
        technologies=[],
        posted_at=parse_datetime(raw.raw_posted_date),
        updated_at=None,
        retrieved_at=raw.retrieved_at,
        recruiter_name=None,
        recruiter_contact=None,
    )
