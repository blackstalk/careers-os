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

from careers_os.sources.workable.constants import (
    BASE_URL,
    LIST_PARAMS,
    MIN_DELAY_BETWEEN_REQUESTS_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
    account_path,
)

logger = logging.getLogger("careers_os.sources.workable")


class WorkableClientError(RuntimeError):
    """Raised when a request to Workable's job-widget API fails after retries."""


class WorkableAccountNotFoundError(WorkableClientError):
    """Raised when an account slug doesn't exist (404)."""


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


class WorkableClient:
    """Thin, respectful client for Workable's public job-widget API.

    Same operational hygiene as the other source clients: honest
    User-Agent, timeout, retry with backoff, self-throttling.
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

    def __enter__(self) -> "WorkableClient":
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
            logger.warning("workable.request_failed", extra={"path": path, "error": str(exc)})
            raise

        if response.status_code == 404:
            self.requests_failed += 1
            raise WorkableAccountNotFoundError(f"Not found: {path}")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self.requests_failed += 1
            logger.warning("workable.request_failed", extra={"path": path, "error": str(exc)})
            raise WorkableClientError(str(exc)) from exc

        logger.info(
            "workable.request_succeeded",
            extra={"path": path, "duration_seconds": round(time.monotonic() - started, 3)},
        )
        try:
            return response.json()
        except ValueError as exc:
            self.requests_failed += 1
            raise WorkableClientError(f"Non-JSON response from {path}: {exc}") from exc

    def list_jobs(self, account: str) -> dict[str, Any]:
        """Fetch ALL published jobs for an account in one call. No
        server-side search/filter/pagination, same as Greenhouse/Ashby/Lever.
        """
        try:
            return self._get(account_path(account), dict(LIST_PARAMS))
        except WorkableAccountNotFoundError:
            raise
        except WorkableClientError as exc:
            raise WorkableClientError(
                f"Workable job list request failed for account={account}: {exc}"
            ) from exc
