"""Manual posting ingestion (Phase 5).

Some postings are only reachable by hand: an Indeed listing, a job
emailed by a recruiter, a careers page with no supported ATS behind it.
Rather than scraping those, this takes what a human can copy (the URL,
and the description text) and runs it through exactly the same
normalize -> persist -> score -> evaluate pipeline every adapter uses.

Two deliberate choices:

- `source="manual"` with a URL-derived `source_job_id`, so re-adding the
  same URL updates the same row instead of creating a duplicate, and so
  the existing `(source, source_job_id)` identity rules apply unchanged.
- `--canonical-url` records the employer's own posting when it is known.
  When the authoritative Greenhouse/Ashby/Lever/Workable posting is later
  discovered on its own, the existing cross-source duplicate scan links
  the two records and `sources/authority.py` prefers the employer copy.
  Nothing is merged or overwritten.
"""

import hashlib
import re
from datetime import datetime, timezone
from typing import Optional

from careers_os.domain.enums import EmploymentType, RemoteStatus
from careers_os.domain.job import NormalizedJob
from careers_os.domain.raw_job import RawJob
from careers_os.storage.db import JobRecord
from careers_os.storage.repository import JobRepository

MANUAL_SOURCE = "manual"

_REMOTE_HINTS = ("fully remote", "100% remote", "remote-first", "work from home", "remote (us")
_HYBRID_HINTS = ("hybrid",)
_ONSITE_HINTS = ("on-site", "onsite", "in-office")
_SALARY = re.compile(r"\$\s?(\d{2,3}(?:,\d{3})?)(?:\s?[kK])?\s?(?:-|–|to)\s?\$?\s?(\d{2,3}(?:,\d{3})?)(?:\s?[kK])?")


def posting_id(url: str) -> str:
    """Stable id for a URL so the same posting re-added later updates in
    place rather than duplicating."""
    return hashlib.sha256(url.strip().lower().encode("utf-8")).hexdigest()[:16]


def _detect_remote(text: str) -> RemoteStatus:
    low = text.lower()
    if any(h in low for h in _REMOTE_HINTS):
        return RemoteStatus.REMOTE
    if any(h in low for h in _HYBRID_HINTS):
        return RemoteStatus.HYBRID
    if any(h in low for h in _ONSITE_HINTS):
        return RemoteStatus.ONSITE
    return RemoteStatus.UNKNOWN


def _detect_salary(text: str) -> tuple[Optional[float], Optional[float]]:
    """A salary range when the text states one plainly. Conservative: no
    match means unknown, which the pipeline treats as "needs
    verification", never as a failure."""
    match = _SALARY.search(text)
    if not match:
        return None, None

    def value(raw: str, whole: str) -> float:
        number = float(raw.replace(",", ""))
        return number * 1000 if ("k" in whole.lower() and number < 1000) else number

    low = value(match.group(1), match.group(0))
    high = value(match.group(2), match.group(0))
    if low > high:
        low, high = high, low
    # Hourly-looking figures are left alone rather than guessed at.
    return (low, high) if low >= 1000 else (None, None)


def build_manual_job(
    *,
    url: str,
    description: str,
    title: str,
    company: Optional[str] = None,
    canonical_url: Optional[str] = None,
    location: Optional[str] = None,
    employment_type: EmploymentType = EmploymentType.UNKNOWN,
    remote_status: Optional[RemoteStatus] = None,
    salary_min: Optional[float] = None,
    salary_max: Optional[float] = None,
) -> tuple[RawJob, NormalizedJob]:
    retrieved_at = datetime.now(timezone.utc)
    detected_min, detected_max = _detect_salary(description)
    source_job_id = posting_id(canonical_url or url)
    payload = {
        "entered_url": url,
        "canonical_url": canonical_url,
        "entered_at": retrieved_at.isoformat(),
    }
    raw = RawJob(
        source=MANUAL_SOURCE, source_job_id=source_job_id, source_url=canonical_url or url,
        raw_title=title, raw_location=location,
        raw_description=description, raw_payload=payload, retrieved_at=retrieved_at,
    )
    normalized = NormalizedJob(
        source=MANUAL_SOURCE, source_job_id=source_job_id, source_url=canonical_url or url,
        title=title, company=company, location=location,
        remote_status=remote_status or _detect_remote(f"{location or ''}\n{description}"),
        employment_type=employment_type,
        salary_min=salary_min if salary_min is not None else detected_min,
        salary_max=salary_max if salary_max is not None else detected_max,
        description=description, retrieved_at=retrieved_at,
    )
    return raw, normalized


def ingest_manual_posting(repository: JobRepository, raw: RawJob, normalized: NormalizedJob) -> tuple[JobRecord, bool]:
    """Persist through the ordinary upsert so status, first-seen, change
    history, and the cross-source duplicate scan all behave normally."""
    record, is_new = repository.upsert_job(normalized, raw)
    repository.commit()
    return record, is_new
