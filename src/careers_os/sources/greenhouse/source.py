import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from careers_os.domain.enums import SourceHealthStatus
from careers_os.domain.query import JobSearchQuery, SortOrder
from careers_os.domain.raw_job import RawJob
from careers_os.sources.base import JobSource, SourceHealth
from careers_os.sources.greenhouse import parser
from careers_os.sources.greenhouse.client import (
    GreenhouseBoardNotFoundError,
    GreenhouseClient,
    GreenhouseClientError,
)

logger = logging.getLogger("careers_os.sources.greenhouse")


class GreenhouseSource(JobSource):
    """Second source adapter: Greenhouse's public Job Board API.

    Unlike CreativeCircleSource, one instance is scoped to a single board
    (company) — Greenhouse is inherently multi-tenant/company-specific, and
    a board token is required to know which company's postings to fetch or
    which endpoint `fetch_job` should hit (see
    `parser.make_source_job_id`/`split_source_job_id`). Searching multiple
    boards means instantiating multiple `GreenhouseSource`s — see
    `cli.py`'s `search greenhouse-all` — rather than baking multi-board
    fan-out into the adapter itself.

    `NormalizedJob.source` is always the constant `"greenhouse"` (not
    `"greenhouse:<board>"`) so that `(source, source_job_id)` deduplication
    stays consistent with docs/architecture.md — the board token instead
    lives inside `source_job_id` (`"<board>:<id>"`), keeping IDs unique
    without redefining what "source" means.

    Almost everything here is client-side, because the Job Board API has no
    server-side search, filter, or pagination parameters at all — see
    docs/sources/greenhouse.md.
    """

    def __init__(self, board_token: str, client: Optional[GreenhouseClient] = None) -> None:
        self.board_token = board_token
        self.name = f"greenhouse:{board_token}"
        self._client = client or GreenhouseClient()
        self._owns_client = client is None
        self.parse_failures = 0
        self.last_error: Optional[str] = None
        self._notes: list[str] = []

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "GreenhouseSource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _relevance_key(self, raw: RawJob, keyword: str) -> int:
        keyword = keyword.lower()
        title_hits = (raw.raw_title or "").lower().count(keyword)
        body_hits = (raw.raw_description or "").lower().count(keyword)
        return title_hits * 5 + body_hits  # title mentions weighted far above body mentions

    def search(self, query: JobSearchQuery) -> list[RawJob]:
        try:
            response = self._client.list_jobs(self.board_token)
        except GreenhouseBoardNotFoundError as exc:
            self.last_error = str(exc)
            logger.error(
                "greenhouse.board_not_found", extra={"board": self.board_token}
            )
            raise
        except GreenhouseClientError as exc:
            self.last_error = str(exc)
            logger.error(
                "greenhouse.search_failed", extra={"board": self.board_token, "error": str(exc)}
            )
            raise

        items = response.get("jobs", [])
        logger.info(
            "greenhouse.list_completed",
            extra={"board": self.board_token, "total": response.get("meta", {}).get("total", len(items))},
        )

        raw_jobs: list[RawJob] = []
        retrieved_at = datetime.now(timezone.utc)
        for item in items:
            try:
                raw_jobs.append(
                    parser.parse_job_payload_to_raw_job(
                        item, self.board_token, retrieved_at=retrieved_at
                    )
                )
            except (ValueError, KeyError, TypeError) as exc:
                self.parse_failures += 1
                self.last_error = str(exc)
                logger.error(
                    "greenhouse.parse_failed",
                    extra={"board": self.board_token, "item_id": item.get("id"), "error": str(exc)},
                )
                continue

        # Everything below is client-side filtering — see class docstring.
        if query.keyword:
            kw = query.keyword.lower()
            raw_jobs = [
                r
                for r in raw_jobs
                if kw in (r.raw_title or "").lower() or kw in (r.raw_description or "").lower()
            ]
        if query.location:
            loc = query.location.lower()
            raw_jobs = [r for r in raw_jobs if loc in (r.raw_location or "").lower()]
        if query.remote_only:
            raw_jobs = [
                r
                for r in raw_jobs
                if parser.detect_remote_status(r.raw_payload).value == "remote"
            ]
        if query.employment_type is not None:
            raw_jobs = [
                r
                for r in raw_jobs
                if parser.detect_employment_type(r.raw_payload) == query.employment_type
            ]
        if query.posted_within_days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=query.posted_within_days)
            kept = []
            for r in raw_jobs:
                posted = parser.parse_datetime(r.raw_posted_date)
                if posted is None or posted >= cutoff:
                    kept.append(r)
            raw_jobs = kept

        if query.sort == SortOrder.DATE:
            raw_jobs.sort(
                key=lambda r: parser.parse_datetime(r.raw_posted_date) or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
        elif query.keyword:
            raw_jobs.sort(key=lambda r: self._relevance_key(r, query.keyword), reverse=True)
        # else: no query and no date sort requested -> whatever order the API returned.

        start = (query.page - 1) * query.page_size
        page = raw_jobs[start : start + query.page_size]

        if not items:
            self._notes.append(f"Board '{self.board_token}' returned zero jobs.")
        elif query.keyword and not page and not raw_jobs:
            self._notes.append(
                f"Zero results for keyword={query.keyword!r} on board '{self.board_token}' "
                f"out of {len(items)} total postings — filtering is entirely client-side for "
                "Greenhouse (no server-side search), so this reflects the board's real content."
            )

        return page

    def fetch_job(self, source_job_id: str) -> RawJob:
        board_token, job_id = parser.split_source_job_id(source_job_id)
        try:
            detail = self._client.get_job_detail(board_token, job_id)
        except GreenhouseClientError as exc:
            self.last_error = str(exc)
            logger.error(
                "greenhouse.fetch_job_failed",
                extra={"source_job_id": source_job_id, "error": str(exc)},
            )
            raise
        try:
            return parser.parse_job_payload_to_raw_job(detail, board_token)
        except (ValueError, KeyError, TypeError) as exc:
            self.parse_failures += 1
            self.last_error = str(exc)
            logger.error(
                "greenhouse.parse_failed",
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
