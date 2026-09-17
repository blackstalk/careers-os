import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from careers_os.domain.enums import SourceHealthStatus
from careers_os.domain.query import JobSearchQuery
from careers_os.domain.raw_job import RawJob
from careers_os.sources.base import JobSource, SourceHealth
from careers_os.sources.himalayas import parser
from careers_os.sources.himalayas.client import HimalayasClient, HimalayasClientError
from careers_os.sources.himalayas.constants import PAGE_SIZE, PAGES_PER_QUERY

logger = logging.getLogger("careers_os.sources.himalayas")


class HimalayasSource(JobSource):
    """Sixth source adapter and the first aggregator with real keyword
    search: Himalayas' public remote-jobs API. See docs/sources/himalayas.md.

    Search runs server-side (US-open postings, most recent first) and is
    capped at PAGES_PER_QUERY pages per query. Every listing is remote.
    """

    name = "himalayas"

    def __init__(self, client: Optional[HimalayasClient] = None, pages_per_query: int = PAGES_PER_QUERY) -> None:
        self._client = client or HimalayasClient()
        self._owns_client = client is None
        self.pages_per_query = pages_per_query
        self.parse_failures = 0
        self.last_error: Optional[str] = None
        self.last_error_kind: Optional[str] = None
        self._notes: list[str] = []

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "HimalayasSource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _fetch(self, keyword: str) -> list[RawJob]:
        seen: set[str] = set()
        raw_jobs: list[RawJob] = []
        retrieved_at = datetime.now(timezone.utc)
        total = None
        for page in range(1, self.pages_per_query + 1):
            try:
                data = self._client.search(keyword, page)
            except HimalayasClientError as exc:
                self.last_error, self.last_error_kind = str(exc), exc.kind
                logger.error("himalayas.search_failed", extra={"keyword": keyword, "page": page, "kind": exc.kind})
                raise
            total = data.get("totalCount", total)
            items = data["jobs"]
            for item in items:
                try:
                    raw = parser.parse_job_payload_to_raw_job(item, retrieved_at=retrieved_at)
                except (ValueError, KeyError, TypeError) as exc:
                    self.parse_failures += 1
                    self.last_error, self.last_error_kind = str(exc), "parse_failed"
                    logger.error("himalayas.parse_failed", extra={"error": str(exc)})
                    continue
                if raw.source_job_id in seen:
                    continue
                seen.add(raw.source_job_id)
                raw_jobs.append(raw)
            if not items:
                break
        if not raw_jobs and total == 0:
            self._notes.append(f"Himalayas is healthy but has zero US-open matches for {keyword!r}.")
        return raw_jobs

    def search(self, query: JobSearchQuery) -> list[RawJob]:
        raw_jobs = self._fetch(query.keyword or "")

        if query.location:
            loc = query.location.lower()
            raw_jobs = [r for r in raw_jobs if loc in (r.raw_location or "").lower()]
        if query.employment_type is not None:
            raw_jobs = [r for r in raw_jobs if parser.detect_employment_type(r.raw_payload) == query.employment_type]
        if query.posted_within_days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=query.posted_within_days)
            raw_jobs = [
                r for r in raw_jobs
                if not r.raw_posted_date or datetime.fromisoformat(r.raw_posted_date) >= cutoff
            ]
        return raw_jobs[: max(query.page_size, PAGE_SIZE * self.pages_per_query)]

    def fetch_job(self, source_job_id: str) -> RawJob:
        """No detail endpoint exists; re-search by the company slug and
        match the id."""
        company, _, _ = source_job_id.partition(":")
        for raw in self._fetch(company.replace("-", " ")):
            if raw.source_job_id == source_job_id:
                return raw
        raise HimalayasClientError(f"Himalayas job {source_job_id!r} not found")

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
        notes = list(self._notes)
        if self.last_error_kind:
            notes.append(f"last error kind: {self.last_error_kind}")
        return SourceHealth(
            source=self.name,
            status=status,
            requests_attempted=attempted,
            requests_failed=failed,
            parse_failures=self.parse_failures,
            last_error=self.last_error,
            notes=notes,
        )
