import logging
import threading
import time
from typing import Any, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from careers_os.sources.ashby.constants import (
    BASE_URL,
    MIN_DELAY_BETWEEN_REQUESTS_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
    job_board_path,
)

logger = logging.getLogger("careers_os.sources.ashby")


class AshbyClientError(RuntimeError):
    """Raised when a request to Ashby's public Job Postings API fails after retries."""


class AshbyBoardNotFoundError(AshbyClientError):
    """Raised when a job-board name doesn't exist (404)."""


class _RateLimiter:
    """See CreativeCircleClient._RateLimiter — identical, minimum-delay throttle."""

    def __init__(self, min_delay_seconds: float) -> None:
        self._min_delay = min_delay_seconds
        self._lock = threading.Lock()
        self._last_request_at: Optional[float] = None

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._last_request_at is not None:
                remaining = self._min_delay - (now - self._last_request_at)
                if remaining > 0:
                    time.sleep(remaining)
            self._last_request_at = time.monotonic()


class AshbyClient:
    """Thin, respectful client for Ashby's public Job Postings API.

    A genuinely documented, unauthenticated public API
    (https://developers.ashbyhq.com/docs/public-job-posting-api),
    explicitly intended for building external job listings/feeds — same
    operational hygiene as the other source clients: honest User-Agent,
    timeout, retry with backoff, self-throttling.
    """

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        min_delay_seconds: float = MIN_DELAY_BETWEEN_REQUESTS_SECONDS,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            transport=transport,
        )
        self._rate_limiter = _RateLimiter(min_delay_seconds)
        self.requests_attempted = 0
        self.requests_failed = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AshbyClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(httpx.TransportError),
    )
    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self._rate_limiter.wait()
        self.requests_attempted += 1
        started = time.monotonic()
        try:
            response = self._client.get(path, params=params)
        except httpx.TransportError as exc:
            self.requests_failed += 1
            logger.warning(
                "ashby.request_failed",
                extra={"path": path, "params": params, "error": str(exc)},
            )
            raise

        if response.status_code == 404:
            self.requests_failed += 1
            raise AshbyBoardNotFoundError(f"Not found: {path}")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self.requests_failed += 1
            logger.warning(
                "ashby.request_failed",
                extra={"path": path, "params": params, "error": str(exc)},
            )
            raise AshbyClientError(str(exc)) from exc

        duration = time.monotonic() - started
        logger.info(
            "ashby.request_succeeded",
            extra={"path": path, "params": params, "duration_seconds": round(duration, 3)},
        )
        try:
            return response.json()
        except ValueError as exc:
            self.requests_failed += 1
            raise AshbyClientError(f"Non-JSON response from {path}: {exc}") from exc

    def list_jobs(self, board_name: str) -> dict[str, Any]:
        """Fetch ALL published postings for a job board in one call.

        Like Greenhouse, there is no server-side search, filter, or
        pagination — `includeCompensation=true` is the only supported
        query param, and it's always requested since structured
        compensation is this source's main advantage over the others.
        """
        try:
            return self._get(job_board_path(board_name), {"includeCompensation": "true"})
        except AshbyBoardNotFoundError:
            raise
        except AshbyClientError as exc:
            raise AshbyClientError(
                f"Ashby job board request failed for board={board_name}: {exc}"
            ) from exc
