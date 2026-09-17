import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from careers_os.domain.enums import SourceHealthStatus
from careers_os.domain.query import JobSearchQuery, SortOrder
from careers_os.domain.raw_job import RawJob
from careers_os.sources.base import JobSource, SourceHealth
from careers_os.sources.workable import parser
from careers_os.sources.workable.client import (
    WorkableAccountNotFoundError,
    WorkableClient,
    WorkableClientError,
)

logger = logging.getLogger("careers_os.sources.workable")


class WorkableSource(JobSource):
    """Fifth source adapter: Workable's public job-widget API.

    Same shape as AshbySource — one instance per company account slug,
    `NormalizedJob.source` always the constant `"workable"`, all
    filtering client-side, and `fetch_job` re-lists the account because
    there is no per-job detail endpoint. See docs/sources/workable.md.
    """

    name = "workable"

    def __init__(self, account: str, client: Optional[WorkableClient] = None) -> None:
        self.account = account
        self.name = f"workable:{account}"
        self._client = client or WorkableClient()
        self._owns_client = client is None
        self.parse_failures = 0
        self.last_error: Optional[str] = None
        self._notes: list[str] = []
        self._listing = None  # one board download per instance/run; boards list every job at once

    def _board_listing(self):
        if self._listing is None:
            self._listing = self._client.list_jobs(self.account)
        return self._listing

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "WorkableSource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _relevance_key(self, raw: RawJob, keyword: str) -> int:
        keyword = keyword.lower()
        return (raw.raw_title or "").lower().count(keyword) * 5 + (raw.raw_description or "").lower().count(keyword)

    def _list_raw_jobs(self, account: str) -> list[RawJob]:
        response = self._board_listing() if account == self.account else self._client.list_jobs(account)
        items = response.get("jobs", [])
        company_name = response.get("name")
        logger.info("workable.list_completed", extra={"account": account, "total": len(items)})

        raw_jobs: list[RawJob] = []
        retrieved_at = datetime.now(timezone.utc)
        for item in items:
            try:
                raw_jobs.append(
                    parser.parse_job_payload_to_raw_job(item, account, company_name, retrieved_at=retrieved_at)
                )
            except (ValueError, KeyError, TypeError) as exc:
                self.parse_failures += 1
                self.last_error = str(exc)
                logger.error(
                    "workable.parse_failed",
                    extra={"account": account, "shortcode": item.get("shortcode"), "error": str(exc)},
                )
        if not items:
            self._notes.append(f"Account '{account}' returned zero jobs.")
        return raw_jobs

    def search(self, query: JobSearchQuery) -> list[RawJob]:
        try:
            raw_jobs = self._list_raw_jobs(self.account)
        except WorkableAccountNotFoundError as exc:
            self.last_error = str(exc)
            logger.error("workable.account_not_found", extra={"account": self.account})
            raise
        except WorkableClientError as exc:
            self.last_error = str(exc)
            logger.error("workable.search_failed", extra={"account": self.account, "error": str(exc)})
            raise

        total_before_filters = len(raw_jobs)

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
            raw_jobs = [
                r for r in raw_jobs
                if (posted := parser.parse_datetime(r.raw_posted_date)) is None or posted >= cutoff
            ]

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
                f"Zero results for keyword={query.keyword!r} on account '{self.account}' "
                f"out of {total_before_filters} total postings."
            )
        return page

    def fetch_job(self, source_job_id: str) -> RawJob:
        account, shortcode = parser.split_source_job_id(source_job_id)
        try:
            raw_jobs = self._list_raw_jobs(account)
        except WorkableClientError as exc:
            self.last_error = str(exc)
            logger.error("workable.fetch_job_failed", extra={"source_job_id": source_job_id, "error": str(exc)})
            raise
        match = next((r for r in raw_jobs if r.source_job_id == source_job_id), None)
        if match is None:
            raise WorkableClientError(f"Job {shortcode!r} not found on account {account!r}")
        return match

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
