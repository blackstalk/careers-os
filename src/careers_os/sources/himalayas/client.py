import logging
import threading
import time
from typing import Any, Optional

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from careers_os.sources.himalayas.constants import (
    BASE_URL,
    COUNTRY,
    MIN_DELAY_BETWEEN_REQUESTS_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    SEARCH_PATH,
    USER_AGENT,
)

logger = logging.getLogger("careers_os.sources.himalayas")


class HimalayasClientError(RuntimeError):
    """Base class; `kind` says what went wrong for source-health reporting."""

    kind = "request_failed"


class HimalayasRateLimitedError(HimalayasClientError):
    kind = "rate_limited"


class HimalayasSchemaError(HimalayasClientError):
    """The response parsed but no longer has the shape this adapter expects —
    most likely an upstream API change. Raised instead of silently
    returning zero jobs."""

    kind = "schema_changed"


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


class HimalayasClient:
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

    def __enter__(self) -> "HimalayasClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=16),
        retry=retry_if_exception_type((httpx.TransportError, HimalayasRateLimitedError)),
    )
    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self._rate_limiter.wait()
        self.requests_attempted += 1
        try:
            response = self._client.get(path, params=params)
        except httpx.TransportError as exc:
            self.requests_failed += 1
            logger.warning("himalayas.request_failed", extra={"params": params, "error": str(exc)})
            raise

        if response.status_code == 429:
            self.requests_failed += 1
            logger.warning("himalayas.rate_limited", extra={"params": params})
            raise HimalayasRateLimitedError("Himalayas rate limit hit (HTTP 429)")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self.requests_failed += 1
            raise HimalayasClientError(str(exc)) from exc

        try:
            data = response.json()
        except ValueError as exc:
            self.requests_failed += 1
            raise HimalayasSchemaError(f"Non-JSON response from Himalayas: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            self.requests_failed += 1
            raise HimalayasSchemaError("Himalayas response has no 'jobs' list — API shape changed?")
        return data

    def search(self, keyword: str, page: int) -> dict[str, Any]:
        params: dict[str, Any] = {"country": COUNTRY, "sort": "recent", "page": page}
        if keyword:
            params["q"] = keyword
        return self._get(SEARCH_PATH, params)
