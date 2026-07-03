from __future__ import annotations

from collections.abc import Callable
import random
import sys
import time
from typing import TypeVar


T = TypeVar("T")

RETRYABLE_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class TransientHttpError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


def retry_transient(
    action: Callable[[], T],
    *,
    label: str,
    retries: int,
    base_delay_seconds: float,
    max_delay_seconds: float,
) -> T:
    attempts = max(0, retries) + 1
    for attempt in range(attempts):
        try:
            return action()
        except Exception as exc:
            if attempt >= attempts - 1 or not is_retryable_exception(exc):
                raise

            wait = _retry_delay_seconds(
                attempt=attempt,
                base_delay_seconds=base_delay_seconds,
                max_delay_seconds=max_delay_seconds,
                retry_after_seconds=getattr(exc, "retry_after_seconds", None),
            )
            print(
                f"{label}: transient error; retry {attempt + 1}/{retries} "
                f"in {wait:.1f}s ({exc})",
                file=sys.stderr,
            )
            time.sleep(wait)

    raise RuntimeError("unreachable retry state")


def is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, TransientHttpError):
        return True

    message = str(exc).lower()
    retryable_tokens = (
        "429",
        "too many requests",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "remote end closed",
        "http 408",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
    )
    return any(token in message for token in retryable_tokens)


def raise_for_http_response(response: object, *, provider: str, context: str) -> None:
    status_code = _status_code(response)
    if status_code < 400:
        return

    text = str(getattr(response, "text", ""))[:120]
    message = f"{provider} {context} HTTP {status_code}: {text}"
    if status_code in RETRYABLE_HTTP_STATUS_CODES:
        raise TransientHttpError(
            message,
            status_code=status_code,
            retry_after_seconds=_retry_after_seconds(response),
        )
    raise RuntimeError(message)


def _retry_delay_seconds(
    *,
    attempt: int,
    base_delay_seconds: float,
    max_delay_seconds: float,
    retry_after_seconds: float | None,
) -> float:
    backoff = max(0.0, base_delay_seconds) * (2**attempt)
    if retry_after_seconds is not None:
        backoff = max(backoff, retry_after_seconds)

    capped = min(max_delay_seconds, backoff) if max_delay_seconds > 0 else backoff
    jitter = random.uniform(0.8, 1.2)
    return max(0.0, capped * jitter)


def _retry_after_seconds(response: object) -> float | None:
    headers = getattr(response, "headers", {}) or {}
    value = None
    if hasattr(headers, "get"):
        value = headers.get("Retry-After") or headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _status_code(response: object) -> int:
    try:
        return int(getattr(response, "status_code", 0) or 0)
    except (TypeError, ValueError):
        return 0
