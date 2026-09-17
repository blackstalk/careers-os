import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from careers_os.domain.enums import SourceHealthStatus
from careers_os.domain.query import JobSearchQuery, SortOrder
from careers_os.domain.raw_job import RawJob
from careers_os.sources.ashby import parser
from careers_os.sources.ashby.client import (
    AshbyBoardNotFoundError,
    AshbyClient,
    AshbyClientError,
)
from careers_os.sources.base import JobSource, SourceHealth

logger = logging.getLogger("careers_os.sources.ashby")


class AshbySource(JobSource):
    """Third source adapter: Ashby's public Job Postings API.

    Same shape as GreenhouseSource — multi-tenant, one instance per
    company job-board name (see `parser.make_source_job_id`/
    `split_source_job_id`). `NormalizedJob.source` is always the constant
    `"ashby"` (not `"ashby:<board>"`), consistent with how Greenhouse
    keeps cross-source `(source, source_job_id)` dedup meaningful
    (docs/architecture.md) — the board name lives inside `source_job_id`
    instead.

    Unlike Greenhouse, there is no per-job detail endpoint at all
    (https://developers.ashbyhq.com/docs/public-job-posting-api) — the
    list endpoint already returns full descriptions and structured
    compensation, so `fetch_job` re-fetches the board's full list and
    finds the matching id, rather than hitting a dedicated URL. See
    docs/sources/ashby.md.
    """

    name = "ashby"

    def __init__(self, board_name: str, client: Optional[AshbyClient] = None) -> None:
        self.board_name = board_name
        self.name = f"ashby:{board_name}"
        self._client = client or AshbyClient()
        self._owns_client = client is None
        self.parse_failures = 0
        self.last_error: Optional[str] = None
        self._notes: list[str] = []
        self._listing = None  # one board download per instance/run; boards list every job at once

    def _board_listing(self):
        if self._listing is None:
            self._listing = self._client.list_jobs(self.board_name)
        return self._listing

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "AshbySource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _relevance_key(self, raw: RawJob, keyword: str) -> int:
        keyword = keyword.lower()
        title_hits = (raw.raw_title or "").lower().count(keyword)
        body_hits = (raw.raw_description or "").lower().count(keyword)
        return title_hits * 5 + body_hits

    def _list_raw_jobs(self) -> list[RawJob]:
        response = self._board_listing()
        items = response.get("jobs", [])
        logger.info("ashby.list_completed", extra={"board": self.board_name, "total": len(items)})

        raw_jobs: list[RawJob] = []
        retrieved_at = datetime.now(timezone.utc)
        for item in items:
            try:
                raw_jobs.append(parser.parse_job_payload_to_raw_job(item, self.board_name, retrieved_at=retrieved_at))
            except (ValueError, KeyError, TypeError) as exc:
                self.parse_failures += 1
                self.last_error = str(exc)
                logger.error(
                    "ashby.parse_failed",
                    extra={"board": self.board_name, "item_id": item.get("id"), "error": str(exc)},
                )
                continue

        if not items:
            self._notes.append(f"Board '{self.board_name}' returned zero jobs.")
        return raw_jobs

    def search(self, query: JobSearchQuery) -> list[RawJob]:
        try:
            raw_jobs = self._list_raw_jobs()
        except AshbyBoardNotFoundError as exc:
            self.last_error = str(exc)
            logger.error("ashby.board_not_found", extra={"board": self.board_name})
            raise
        except AshbyClientError as exc:
            self.last_error = str(exc)
            logger.error("ashby.search_failed", extra={"board": self.board_name, "error": str(exc)})
            raise

        total_before_filters = len(raw_jobs)

        # Everything below is client-side filtering — Ashby's public API
        # has no server-side search/filter/pagination, same as Greenhouse.
        if query.keyword:
            kw = query.keyword.lower()
            raw_jobs = [
                r for r in raw_jobs
                if kw in (r.raw_title or "").lower() or kw in (r.raw_description or "").lower()
            ]
        if query.location:
            loc = query.location.lower()
            raw_jobs = [r for r in raw_jobs if loc in (r.raw_location or "").lower()]
        if query.remote_only:
            raw_jobs = [r for r in raw_jobs if parser.detect_remote_status(r.raw_payload).value == "remote"]
        if query.employment_type is not None:
            raw_jobs = [r for r in raw_jobs if parser.detect_employment_type(r.raw_payload) == query.employment_type]
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

        start = (query.page - 1) * query.page_size
        page = raw_jobs[start : start + query.page_size]

        if total_before_filters and query.keyword and not raw_jobs:
            self._notes.append(
                f"Zero results for keyword={query.keyword!r} on board '{self.board_name}' "
                f"out of {total_before_filters} total postings."
            )

        return page

    def fetch_job(self, source_job_id: str) -> RawJob:
        board_name, job_id = parser.split_source_job_id(source_job_id)
        try:
            response = self._client.list_jobs(board_name)
        except AshbyClientError as exc:
            self.last_error = str(exc)
            logger.error("ashby.fetch_job_failed", extra={"source_job_id": source_job_id, "error": str(exc)})
            raise

        item = next((j for j in response.get("jobs", []) if str(j.get("id")) == job_id), None)
        if item is None:
            raise AshbyClientError(f"Job {job_id!r} not found on board {board_name!r}")

        try:
            return parser.parse_job_payload_to_raw_job(item, board_name)
        except (ValueError, KeyError, TypeError) as exc:
            self.parse_failures += 1
            self.last_error = str(exc)
            logger.error("ashby.parse_failed", extra={"source_job_id": source_job_id, "error": str(exc)})
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
