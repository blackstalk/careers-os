"""Parsing/normalization for Greenhouse's Job Board API payloads.

All Greenhouse-specific field names and heuristics live only in this file
(and constants.py) — see careers_os.sources.base.JobSource for why that
matters.
"""

import html
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.job import NormalizedJob
from careers_os.domain.raw_job import RawJob
from careers_os.sources.greenhouse.constants import (
    EMPLOYMENT_TYPE_METADATA_LABELS,
    EMPLOYMENT_TYPE_TEXT_MARKERS,
    LOCATION_TEXT_REMOTE_MARKERS,
    REMOTE_METADATA_LABELS,
    REMOTE_VALUE_TO_STATUS,
    SALARY_TEXT_PATTERN,
)

logger = logging.getLogger("careers_os.sources.greenhouse")

SOURCE_NAME = "greenhouse"

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def make_source_job_id(board_token: str, greenhouse_job_id: Any) -> str:
    """Greenhouse's per-board endpoints need the board token to fetch detail,
    so it's folded into our source_job_id (`{board_token}:{id}`) rather than
    kept only as adapter-instance state — see source.py for why.
    """
    return f"{board_token}:{greenhouse_job_id}"


def split_source_job_id(source_job_id: str) -> tuple[str, str]:
    board_token, _, job_id = source_job_id.partition(":")
    if not board_token or not job_id:
        raise ValueError(f"Malformed Greenhouse source_job_id: {source_job_id!r}")
    return board_token, job_id


def html_to_text(raw_html: Optional[str]) -> Optional[str]:
    """Greenhouse's `content` field is HTML that has been HTML-entity-escaped
    an extra time (literal `&lt;div&gt;...`) — see docs/sources/greenhouse.md.
    Unescape once to get real HTML, unescape again to resolve entities
    inside it (e.g. `&amp;mdash;` -> `—`), then strip tags.
    """
    if not raw_html:
        return None
    unescaped = html.unescape(html.unescape(raw_html))
    text = _TAG_RE.sub(" ", unescaped)
    text = html.unescape(text)
    return _WHITESPACE_RE.sub(" ", text).strip() or None


def _location_string(payload: dict[str, Any]) -> Optional[str]:
    location = payload.get("location") or {}
    name = location.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def _metadata_lookup(payload: dict[str, Any], labels: set[str]) -> Optional[str]:
    for entry in payload.get("metadata") or []:
        name = str(entry.get("name", "")).strip().lower()
        if name in labels and entry.get("value"):
            return str(entry["value"])
    return None


def detect_remote_status(payload: dict[str, Any]) -> RemoteStatus:
    """Best-effort only — Greenhouse has no standardized remote-status field.
    See docs/sources/greenhouse.md "Known limitations".
    """
    metadata_value = _metadata_lookup(payload, REMOTE_METADATA_LABELS)
    if metadata_value:
        mapped = REMOTE_VALUE_TO_STATUS.get(metadata_value.strip().lower())
        if mapped:
            return RemoteStatus(mapped)

    location_text = (_location_string(payload) or "").lower()
    if any(marker in location_text for marker in LOCATION_TEXT_REMOTE_MARKERS):
        return RemoteStatus.REMOTE

    return RemoteStatus.UNKNOWN


def detect_employment_type(payload: dict[str, Any]) -> EmploymentType:
    """Best-effort only — no standardized field; see module docstring."""
    metadata_value = _metadata_lookup(payload, EMPLOYMENT_TYPE_METADATA_LABELS)
    haystacks = [metadata_value or "", str(payload.get("title") or "")]
    combined = " ".join(haystacks).lower()
    for employment_type, markers in EMPLOYMENT_TYPE_TEXT_MARKERS.items():
        if any(marker in combined for marker in markers):
            return EmploymentType(employment_type)
    return EmploymentType.UNKNOWN


def extract_salary_from_text(text: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    """Low-confidence, best-effort extraction of a salary range embedded in
    free-text job description HTML (observed pattern: "Annual Salary:
    $X — $Y USD"). Never treated as reliably present — see
    docs/sources/greenhouse.md "Known limitations".
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
    payload: dict[str, Any], board_token: str, *, retrieved_at: Optional[datetime] = None
) -> RawJob:
    retrieved_at = retrieved_at or datetime.now(timezone.utc)

    greenhouse_id = payload.get("id")
    if greenhouse_id is None:
        raise ValueError("Job payload is missing an 'id' field")

    description = html_to_text(payload.get("content"))
    departments = [d.get("name") for d in (payload.get("departments") or []) if d.get("name")]
    offices = [o.get("name") for o in (payload.get("offices") or []) if o.get("name")]

    return RawJob(
        source=SOURCE_NAME,
        source_job_id=make_source_job_id(board_token, greenhouse_id),
        source_url=payload.get("absolute_url") or "",
        raw_title=payload.get("title"),
        raw_location=_location_string(payload),
        raw_description=description,
        raw_compensation=None,  # see normalize_raw_job — extracted from description text there
        raw_employment_type=None,  # no reliable raw field; computed heuristically at normalize time
        raw_posted_date=payload.get("first_published"),
        raw_updated_date=payload.get("updated_at"),
        raw_recruiter=None,  # Greenhouse's public Job Board API does not expose recruiter contact info
        raw_payload={**payload, "_board_token": board_token, "_departments": departments, "_offices": offices},
        retrieved_at=retrieved_at,
    )


def parse_datetime(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        logger.warning("greenhouse.unparseable_date", extra={"value": value})
        return None


def normalize_raw_job(raw: RawJob) -> NormalizedJob:
    payload = raw.raw_payload
    board_token = payload.get("_board_token", "")

    salary_min, salary_max = extract_salary_from_text(raw.raw_description)

    title = (raw.raw_title or "").strip() or "(untitled)"

    return NormalizedJob(
        source=raw.source,
        source_job_id=raw.source_job_id,
        source_url=raw.source_url,
        title=title,
        company=payload.get("company_name") or board_token or None,
        location=raw.raw_location,
        remote_status=detect_remote_status(payload),
        employment_type=detect_employment_type(payload),
        salary_min=salary_min,
        salary_max=salary_max,
        hourly_min=None,
        hourly_max=None,
        currency="USD" if salary_min or salary_max else None,
        description=raw.raw_description,
        skills=[],  # Greenhouse's public API exposes no tags/skills field
        technologies=[],
        posted_at=parse_datetime(raw.raw_posted_date),
        updated_at=parse_datetime(raw.raw_updated_date),
        retrieved_at=raw.retrieved_at,
        recruiter_name=None,
        recruiter_contact=None,
    )
