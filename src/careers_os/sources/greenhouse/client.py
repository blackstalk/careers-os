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

from careers_os.sources.greenhouse.constants import (
    BASE_URL,
    MIN_DELAY_BETWEEN_REQUESTS_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
    job_detail_path,
    jobs_list_path,
)

logger = logging.getLogger("careers_os.sources.greenhouse")


class GreenhouseClientError(RuntimeError):
    """Raised when a request to the Greenhouse job board API fails after retries."""


class GreenhouseBoardNotFoundError(GreenhouseClientError):
    """Raised when a board token doesn't exist (404)."""


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


class GreenhouseClient:
    """Thin, respectful client for Greenhouse's public Job Board API.

    Unlike CreativeCircleClient, this talks to a genuinely documented public
    API (https://developers.greenhouse.io/job-board.html) — no undocumented
    endpoints, no reverse engineering. Same operational hygiene as the
    Creative Circle client: honest User-Agent, timeout, retry with backoff,
    self-throttling.
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

    def __enter__(self) -> "GreenhouseClient":
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
                "greenhouse.request_failed",
                extra={"path": path, "params": params, "error": str(exc)},
            )
            raise

        if response.status_code == 404:
            self.requests_failed += 1
            raise GreenhouseBoardNotFoundError(f"Not found: {path}")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self.requests_failed += 1
            logger.warning(
                "greenhouse.request_failed",
                extra={"path": path, "params": params, "error": str(exc)},
            )
            raise GreenhouseClientError(str(exc)) from exc

        duration = time.monotonic() - started
        logger.info(
            "greenhouse.request_succeeded",
            extra={"path": path, "params": params, "duration_seconds": round(duration, 3)},
        )
        try:
            return response.json()
        except ValueError as exc:
            self.requests_failed += 1
            raise GreenhouseClientError(f"Non-JSON response from {path}: {exc}") from exc

    def list_jobs(self, board_token: str) -> dict[str, Any]:
        """Fetch ALL jobs for a board in one call.

        The Job Board API has no pagination, search, or filter parameters
        — `content=true` is the only supported query param, and it controls
        whether `content`/`departments`/`offices` are included at all (see
        docs/sources/greenhouse.md). Client-side filtering/paging is the
        adapter's job, not the server's.
        """
        try:
            return self._get(jobs_list_path(board_token), {"content": "true"})
        except GreenhouseBoardNotFoundError:
            raise
        except GreenhouseClientError as exc:
            raise GreenhouseClientError(
                f"Greenhouse jobs list request failed for board={board_token}: {exc}"
            ) from exc

    def get_job_detail(self, board_token: str, job_id: str) -> dict[str, Any]:
        try:
            return self._get(job_detail_path(board_token, job_id), {})
        except GreenhouseBoardNotFoundError:
            raise
        except GreenhouseClientError as exc:
            raise GreenhouseClientError(
                f"Greenhouse job detail request failed for board={board_token} id={job_id}: {exc}"
            ) from exc
