import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from careers_os.domain.enums import SourceHealthStatus
from careers_os.domain.query import JobSearchQuery, SortOrder
from careers_os.domain.raw_job import RawJob
from careers_os.sources.base import JobSource, SourceHealth
from careers_os.sources.creative_circle import parser
from careers_os.sources.creative_circle.client import (
    CreativeCircleClient,
    CreativeCircleClientError,
)
from careers_os.sources.creative_circle.constants import (
    BUSINESS_UNIT_ID,
    MAX_ROWS,
    REMOTE_WORK_LOCATION_TYPE_ID,
    SORT_TYPE_DATE,
    SORT_TYPE_RELEVANCE,
    SUPPORTED_DAYS_POSTED,
    TAX_TERM_TO_EMPLOYMENT_TYPE,
)

logger = logging.getLogger("careers_os.sources.creative_circle")


def _snap_days_posted(requested: Optional[int]) -> int:
    """Map an arbitrary "posted within N days" request onto the server's
    fixed preset buckets, rounding up so we never under-fetch.

    We always additionally enforce the exact cutoff client-side (see
    `_within_posted_window`) because the server's own bucket semantics were
    observed to be approximate — see docs/sources/creative-circle.md.
    """
    if not requested:
        return 0
    for preset in SUPPORTED_DAYS_POSTED:
        if requested <= preset:
            return preset
    return 0  # requested window wider than any preset; fetch "any" and filter client-side


def _within_posted_window(raw: RawJob, max_days: Optional[int]) -> bool:
    if not max_days:
        return True
    if not raw.raw_posted_date:
        # Unknown post date: don't silently drop it, keep it and let scoring
        # note the missing data rather than pretend we filtered accurately.
        return True
    posted = parser.parse_datetime(raw.raw_posted_date)
    if posted is None:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_days)
    return posted >= cutoff


class CreativeCircleSource(JobSource):
    """First source adapter: Everforth Creative Circle candidate portal.

    Talks to the public, unauthenticated JSON API documented in
    docs/sources/creative-circle.md. See that doc for what was investigated
    and what remains undocumented/unofficial about it.
    """

    name = "creative_circle"

    def __init__(self, client: Optional[CreativeCircleClient] = None) -> None:
        self._client = client or CreativeCircleClient()
        self._owns_client = client is None
        self.parse_failures = 0
        self.last_error: Optional[str] = None
        self._notes: list[str] = []

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "CreativeCircleSource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _build_params(self, query: JobSearchQuery) -> dict[str, object]:
        params: dict[str, object] = {
            "rows": min(query.page_size, MAX_ROWS),
            "page": query.page,
            "sortType": SORT_TYPE_DATE if query.sort == SortOrder.DATE else SORT_TYPE_RELEVANCE,
            "daysPosted": _snap_days_posted(query.posted_within_days),
            "buid": BUSINESS_UNIT_ID,
        }
        if query.keyword:
            params["keyword"] = query.keyword
        if query.location:
            params["locationKeyword"] = query.location
        if query.remote_only:
            params["workLocationTypeId"] = REMOTE_WORK_LOCATION_TYPE_ID

        # Creative Circle does not appear to honor an employment-type filter
        # server-side (termOption/filtertype/positionTypeId were all tested
        # and had no effect — see docs/sources/creative-circle.md). We filter
        # for it client-side in `search()` instead.
        return params

    def search(self, query: JobSearchQuery) -> list[RawJob]:
        params = self._build_params(query)
        try:
            response = self._client.search(params)
        except CreativeCircleClientError as exc:
            self.last_error = str(exc)
            logger.error(
                "creative_circle.search_failed",
                extra={"params": params, "error": str(exc)},
            )
            raise

        items = response.get("jobs", [])
        num_found = response.get("numFound", len(items))
        logger.info(
            "creative_circle.search_completed",
            extra={"params": params, "num_found": num_found, "returned": len(items)},
        )

        raw_jobs: list[RawJob] = []
        retrieved_at = datetime.now(timezone.utc)
        for item in items:
            try:
                raw_jobs.append(
                    parser.parse_job_payload_to_raw_job(item, retrieved_at=retrieved_at)
                )
            except (ValueError, KeyError, TypeError) as exc:
                self.parse_failures += 1
                self.last_error = str(exc)
                logger.error(
                    "creative_circle.parse_failed",
                    extra={"item": item, "error": str(exc)},
                )
                continue

        if query.employment_type is not None:
            raw_jobs = [
                r
                for r in raw_jobs
                if TAX_TERM_TO_EMPLOYMENT_TYPE.get(r.raw_employment_type)
                == query.employment_type
            ]

        max_days = query.posted_within_days
        raw_jobs = [r for r in raw_jobs if _within_posted_window(r, max_days)]

        if num_found == 0 and (query.keyword or query.location):
            self._notes.append(
                f"Zero results for keyword={query.keyword!r} location={query.location!r}. "
                "Creative Circle is a creative/marketing/design staffing brand — "
                "technical roles (e.g. 'solutions architect') are frequently absent "
                "from its catalog entirely; this is expected, not necessarily a bug."
            )

        return raw_jobs

    def fetch_job(self, source_job_id: str) -> RawJob:
        try:
            detail = self._client.get_job_detail(source_job_id)
        except CreativeCircleClientError as exc:
            self.last_error = str(exc)
            logger.error(
                "creative_circle.fetch_job_failed",
                extra={"source_job_id": source_job_id, "error": str(exc)},
            )
            raise
        try:
            return parser.parse_job_payload_to_raw_job(detail)
        except (ValueError, KeyError, TypeError) as exc:
            self.parse_failures += 1
            self.last_error = str(exc)
            logger.error(
                "creative_circle.parse_failed",
                extra={"source_job_id": source_job_id, "error": str(exc)},
            )
            raise

    def normalize(self, raw: RawJob):
        return parser.normalize_raw_job(raw)

    def health(self) -> SourceHealth:
        attempted = self._client.requests_attempted
        failed = self._client.requests_failed
        status = SourceHealthStatus.HEALTHY
        if attempted > 0 and failed == attempted:
            status = SourceHealthStatus.BROKEN
        elif failed > 0 or self.parse_failures > 0:
            status = SourceHealthStatus.DEGRADED
        return SourceHealth(
            source=self.name,
            status=status,
            requests_attempted=attempted,
            requests_failed=failed,
            parse_failures=self.parse_failures,
            last_error=self.last_error,
            notes=list(self._notes),
        )
