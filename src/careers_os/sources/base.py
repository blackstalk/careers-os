from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from careers_os.domain.enums import SourceHealthStatus
from careers_os.domain.query import JobSearchQuery
from careers_os.domain.raw_job import RawJob


class SourceHealth(BaseModel):
    """Self-reported health for one source adapter.

    Kept intentionally simple for Phase 1: a source computes this from its
    own recent request/parse outcomes. It exists so that a future scheduler
    can skip or flag a source before it silently returns garbage.
    """

    source: str
    status: SourceHealthStatus
    requests_attempted: int = 0
    requests_failed: int = 0
    parse_failures: int = 0
    last_error: str | None = None
    notes: list[str] = Field(default_factory=list)


class JobSource(ABC):
    """Contract every source adapter must implement.

    Nothing outside a `sources/<name>/` package may depend on that source's
    URL structure, field names, or HTML/JSON shape. A source adapter's only
    public surface is this interface plus the `RawJob` / `NormalizedJob`
    models it returns.
    """

    name: str

    @abstractmethod
    def search(self, query: JobSearchQuery) -> list[RawJob]:
        """Run a search and return raw (unnormalized) job records."""

    @abstractmethod
    def fetch_job(self, source_job_id: str) -> RawJob:
        """Fetch full detail for a single job by the source's native id."""

    @abstractmethod
    def normalize(self, raw: RawJob):  # -> NormalizedJob
        """Map a RawJob from this source into the canonical schema."""

    @abstractmethod
    def health(self) -> SourceHealth:
        """Report this adapter's current health."""
