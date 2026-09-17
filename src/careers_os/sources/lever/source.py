import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from careers_os.domain.enums import SourceHealthStatus
from careers_os.domain.query import JobSearchQuery, SortOrder
from careers_os.domain.raw_job import RawJob
from careers_os.sources.base import JobSource, SourceHealth
from careers_os.sources.lever import parser
from careers_os.sources.lever.client import (
    LeverClient,
    LeverClientError,
    LeverCompanyNotFoundError,
)

logger = logging.getLogger("careers_os.sources.lever")


class LeverSource(JobSource):
    """Fourth source adapter: Lever's public Postings API.

    Same shape as GreenhouseSource/AshbySource — multi-tenant, one
    instance per company slug (see `parser.make_source_job_id`/
    `split_source_job_id`). `NormalizedJob.source` is always the constant
    `"lever"` (not `"lever:<company>"`), consistent with how the other
    multi-tenant sources keep cross-source `(source, source_job_id)`
    dedup meaningful (docs/architecture.md) — the company slug lives
    inside `source_job_id` instead.

    See docs/sources/lever.md.
    """

    name = "lever"

    def __init__(self, company: str, client: Optional[LeverClient] = None) -> None:
        self.company = company
        self.name = f"lever:{company}"
        self._client = client or LeverClient()
        self._owns_client = client is None
        self.parse_failures = 0
        self.last_error: Optional[str] = None
        self._notes: list[str] = []
        self._listing = None  # one board download per instance/run; boards list every job at once

    def _board_listing(self):
        if self._listing is None:
            self._listing = self._client.list_postings(self.company)
        return self._listing

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "LeverSource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _relevance_key(self, raw: RawJob, keyword: str) -> int:
        keyword = keyword.lower()
        title_hits = (raw.raw_title or "").lower().count(keyword)
        body_hits = (raw.raw_description or "").lower().count(keyword)
        return title_hits * 5 + body_hits

    def search(self, query: JobSearchQuery) -> list[RawJob]:
        try:
            items = self._board_listing()
        except LeverCompanyNotFoundError as exc:
            self.last_error = str(exc)
            logger.error("lever.company_not_found", extra={"company": self.company})
            raise
        except LeverClientError as exc:
            self.last_error = str(exc)
            logger.error("lever.search_failed", extra={"company": self.company, "error": str(exc)})
            raise

        logger.info("lever.list_completed", extra={"company": self.company, "total": len(items)})

        raw_jobs: list[RawJob] = []
        retrieved_at = datetime.now(timezone.utc)
        for item in items:
            try:
                raw_jobs.append(parser.parse_job_payload_to_raw_job(item, self.company, retrieved_at=retrieved_at))
            except (ValueError, KeyError, TypeError) as exc:
                self.parse_failures += 1
                self.last_error = str(exc)
                logger.error(
                    "lever.parse_failed",
                    extra={"company": self.company, "item_id": item.get("id"), "error": str(exc)},
                )
                continue

        total_before_filters = len(raw_jobs)

        # Everything below is client-side filtering — Lever's public
        # Postings API has no server-side keyword search (only structural
        # filters like team/commitment, which JobSearchQuery doesn't
        # model), same limitation as Greenhouse/Ashby.
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

        if not items:
            self._notes.append(f"Company '{self.company}' returned zero jobs.")
        elif total_before_filters and query.keyword and not raw_jobs:
            self._notes.append(
                f"Zero results for keyword={query.keyword!r} on company '{self.company}' "
                f"out of {total_before_filters} total postings."
            )

        return page

    def fetch_job(self, source_job_id: str) -> RawJob:
        company, posting_id = parser.split_source_job_id(source_job_id)
        try:
            detail = self._client.get_posting_detail(company, posting_id)
        except LeverClientError as exc:
            self.last_error = str(exc)
            logger.error("lever.fetch_job_failed", extra={"source_job_id": source_job_id, "error": str(exc)})
            raise
        try:
            return parser.parse_job_payload_to_raw_job(detail, company)
        except (ValueError, KeyError, TypeError) as exc:
            self.parse_failures += 1
            self.last_error = str(exc)
            logger.error("lever.parse_failed", extra={"source_job_id": source_job_id, "error": str(exc)})
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
