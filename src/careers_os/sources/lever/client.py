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

from careers_os.sources.lever.constants import (
    BASE_URL,
    MIN_DELAY_BETWEEN_REQUESTS_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
    posting_detail_path,
    postings_list_path,
)

logger = logging.getLogger("careers_os.sources.lever")


class LeverClientError(RuntimeError):
    """Raised when a request to Lever's public Postings API fails after retries."""


class LeverCompanyNotFoundError(LeverClientError):
    """Raised when a company slug doesn't exist (404)."""


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


class LeverClient:
    """Thin, respectful client for Lever's public Postings API.

    A public, unauthenticated JSON API Lever customers use to power their
    own careers pages (`GET https://api.lever.co/v0/postings/<company>
    ?mode=json`) — same operational hygiene as the other source clients:
    honest User-Agent, timeout, retry with backoff, self-throttling.
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

    def __enter__(self) -> "LeverClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(httpx.TransportError),
    )
    def _get(self, path: str, params: dict[str, Any]) -> Any:
        self._rate_limiter.wait()
        self.requests_attempted += 1
        started = time.monotonic()
        try:
            response = self._client.get(path, params=params)
        except httpx.TransportError as exc:
            self.requests_failed += 1
            logger.warning(
                "lever.request_failed",
                extra={"path": path, "params": params, "error": str(exc)},
            )
            raise

        if response.status_code == 404:
            self.requests_failed += 1
            raise LeverCompanyNotFoundError(f"Not found: {path}")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self.requests_failed += 1
            logger.warning(
                "lever.request_failed",
                extra={"path": path, "params": params, "error": str(exc)},
            )
            raise LeverClientError(str(exc)) from exc

        duration = time.monotonic() - started
        logger.info(
            "lever.request_succeeded",
            extra={"path": path, "params": params, "duration_seconds": round(duration, 3)},
        )
        try:
            return response.json()
        except ValueError as exc:
            self.requests_failed += 1
            raise LeverClientError(f"Non-JSON response from {path}: {exc}") from exc

    def list_postings(self, company: str) -> list[dict[str, Any]]:
        """Fetch ALL open postings for a company in one call.

        Like Greenhouse, there is no server-side search, filter, or
        pagination — `mode=json` is the only supported query param.
        Unlike Ashby/Greenhouse, this returns a bare JSON array, not an
        object wrapping a `jobs`/`postings` key.
        """
        try:
            result = self._get(postings_list_path(company), {"mode": "json"})
        except LeverCompanyNotFoundError:
            raise
        except LeverClientError as exc:
            raise LeverClientError(
                f"Lever postings list request failed for company={company}: {exc}"
            ) from exc
        return result if isinstance(result, list) else []

    def get_posting_detail(self, company: str, posting_id: str) -> dict[str, Any]:
        try:
            return self._get(posting_detail_path(company, posting_id), {"mode": "json"})
        except LeverCompanyNotFoundError:
            raise
        except LeverClientError as exc:
            raise LeverClientError(
                f"Lever posting detail request failed for company={company} id={posting_id}: {exc}"
            ) from exc
