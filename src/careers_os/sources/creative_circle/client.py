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

from careers_os.sources.creative_circle.constants import (
    BASE_URL,
    DETAIL_PATH,
    MIN_DELAY_BETWEEN_REQUESTS_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    SEARCH_PATH,
    USER_AGENT,
)

logger = logging.getLogger("careers_os.sources.creative_circle")


class CreativeCircleClientError(RuntimeError):
    """Raised when a request to the Creative Circle API fails after retries."""


class _RateLimiter:
    """Simple minimum-delay throttle, shared across a client instance.

    Not a token bucket — Phase 1 usage is a handful of sequential requests
    per CLI invocation, so "never fire two requests closer together than
    this" is enough to be a good citizen without adding complexity.
    """

    def __init__(self, min_delay_seconds: float) -> None:
        self._min_delay = min_delay_seconds
        self._lock = threading.Lock()
        self._last_request_at: Optional[float] = None

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._last_request_at is not None:
                elapsed = now - self._last_request_at
                remaining = self._min_delay - elapsed
                if remaining > 0:
                    time.sleep(remaining)
            self._last_request_at = time.monotonic()


class CreativeCircleClient:
    """Thin, respectful HTTP client for the Creative Circle candidate portal.

    This talks to an undocumented-but-public JSON API discovered by
    inspecting the candidate portal's own network traffic (see
    docs/sources/creative-circle.md). It intentionally:
      - identifies itself with a clear, honest User-Agent
      - times every request out
      - retries transient failures with exponential backoff, and gives up
      - self-throttles to a minimum delay between requests

    It does not attempt to bypass authentication or any access control —
    every endpoint it calls is one the site itself serves to anonymous,
    unauthenticated visitors of the public job search page.
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

    def __enter__(self) -> "CreativeCircleClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    )
    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self._rate_limiter.wait()
        self.requests_attempted += 1
        started = time.monotonic()
        try:
            response = self._client.get(path, params=params)
            response.raise_for_status()
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            self.requests_failed += 1
            logger.warning(
                "creative_circle.request_failed",
                extra={"path": path, "params": params, "error": str(exc)},
            )
            raise
        duration = time.monotonic() - started
        logger.info(
            "creative_circle.request_succeeded",
            extra={"path": path, "params": params, "duration_seconds": round(duration, 3)},
        )
        try:
            return response.json()
        except ValueError as exc:
            self.requests_failed += 1
            raise CreativeCircleClientError(
                f"Non-JSON response from {path}: {exc}"
            ) from exc

    def search(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._get(SEARCH_PATH, params)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            raise CreativeCircleClientError(
                f"Creative Circle search request failed: {exc}"
            ) from exc

    def get_job_detail(self, source_job_id: str) -> dict[str, Any]:
        try:
            return self._get(
                DETAIL_PATH, {"id": source_job_id, "buid": 3}
            )
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            raise CreativeCircleClientError(
                f"Creative Circle detail request failed for id={source_job_id}: {exc}"
            ) from exc
