"""Retry policy for model-gateway HTTP calls.

A concurrency ceiling prevents more 429s than any retry strategy can clean up
after, so this module is the second line of defence, not the first. What it
does provide is the part a ceiling cannot: surviving a transient 429 or 5xx
without losing the work already paid for.

Rejected 429 requests still consume quota, so retries are bounded and spaced
with exponential backoff plus full jitter. `Retry-After` wins when the server
sends it, because the server knows better than our curve does.
"""

from __future__ import annotations

import email.utils
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable

#: Bounded so a sustained outage fails instead of retrying forever.
MAX_ATTEMPTS = 5
BASE_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 60.0
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def parse_retry_after(value: str | None) -> float | None:
    """Seconds from a `Retry-After` header, in either permitted form."""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        # A server may send an HTTP-date instead. A malformed value must not
        # take down the retry path it exists to protect.
        try:
            parsed = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if parsed is None:
            return None
        seconds = parsed.timestamp() - time.time()
    if seconds < 0:
        return 0.0
    return min(seconds, MAX_DELAY_SECONDS)


def backoff_delay(attempt: int, *, rng: random.Random | None = None) -> float:
    """Exponential backoff with full jitter for a 1-based attempt number."""
    ceiling = min(BASE_DELAY_SECONDS * (2 ** (attempt - 1)), MAX_DELAY_SECONDS)
    source = rng or random
    return source.uniform(0.0, ceiling)


def urlopen_with_retry(
    request: urllib.request.Request,
    *,
    timeout: float,
    attempts: int = MAX_ATTEMPTS,
    opener: Callable[..., object] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> bytes:
    """Perform one gateway request, retrying transient failures. Returns the body.

    Non-retryable statuses (a 400 from an unsupported `response_format`, a 401,
    a 404) are raised immediately: retrying them burns quota to no purpose.
    """
    open_url = opener or urllib.request.urlopen
    last_error: BaseException | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            with open_url(request, timeout=timeout) as response:  # type: ignore[union-attr]
                return bytes(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE_STATUS or attempt == attempts:
                raise
            last_error = exc
            retry_after = parse_retry_after(exc.headers.get("Retry-After"))
            delay = (
                retry_after
                if retry_after is not None
                else backoff_delay(attempt, rng=rng)
            )
            sleep(delay)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == attempts:
                raise
            last_error = exc
            sleep(backoff_delay(attempt, rng=rng))
    raise RuntimeError("unreachable retry loop") from last_error
