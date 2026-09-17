"""Conservative cross-source duplicate detection.

Same-source deduplication is exact and already solved by
`JobRepository.upsert_job`'s `(source, source_job_id)` key. This module is
for the harder, fuzzier case: the same real-world posting appearing
through two different sources (e.g. a role cross-posted to both a
Creative Circle recruiter and a company's own Greenhouse board) with no
shared identifier at all.

Deliberately conservative per the requirement: this only ever *flags*
possible duplicates for human review (`PossibleDuplicateRecord`) — nothing
here merges or deletes a job record, and a title match alone is never
enough to flag anything.
"""

import difflib
import re
from dataclasses import dataclass

_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company)\.?\b", re.IGNORECASE
)


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def normalize_company(company: str) -> str:
    stripped = _COMPANY_SUFFIXES.sub("", company.lower())
    return re.sub(r"[^a-z0-9]+", " ", stripped).strip()


def normalize_location(location: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", location.lower()).strip()


def description_fingerprint(description: str | None) -> str:
    """First ~300 alphanumeric characters — a cheap, exact key for spotting
    copy-pasted reposts before running the expensive similarity check."""
    if not description:
        return ""
    return re.sub(r"[^a-z0-9]+", "", description.lower())[:300]


def description_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


@dataclass
class DuplicateSignal:
    matched: list[str]
    confidence: float


def compare_jobs(
    title_a: str,
    company_a: str | None,
    location_a: str | None,
    description_a: str | None,
    title_b: str,
    company_b: str | None,
    location_b: str | None,
    description_b: str | None,
) -> DuplicateSignal:
    """Score how likely two job records describe the same real-world posting.

    Never flags on title alone — see module docstring. A flag requires
    either (title + company both match) or a strongly similar description.
    """
    matched: list[str] = []

    title_match = normalize_title(title_a) == normalize_title(title_b)
    if title_match:
        matched.append("title")

    company_match = bool(company_a) and bool(company_b) and (
        normalize_company(company_a) == normalize_company(company_b)
    )
    if company_match:
        matched.append("company")

    location_match = bool(location_a) and bool(location_b) and (
        normalize_location(location_a) == normalize_location(location_b)
    )
    if location_match:
        matched.append("location")

    desc_sim = 0.0
    if description_a and description_b:
        desc_sim = description_similarity(description_a, description_b)
        if desc_sim >= 0.75:
            matched.append("description_similarity")

    if title_match and company_match:
        confidence = 0.95 if location_match else 0.85
    elif desc_sim >= 0.75:
        confidence = 0.6 + 0.2 * location_match
    else:
        confidence = 0.0  # not enough signal to flag at all

    return DuplicateSignal(matched=matched, confidence=round(min(confidence, 0.99), 2))


MIN_CONFIDENCE_TO_FLAG = 0.6
