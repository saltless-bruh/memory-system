"""Tests for gateway retry policy: bounded, jittered, and Retry-After aware."""

from __future__ import annotations

import io
import random
import urllib.error
import urllib.request
from typing import Any

import pytest

from scout.gateway_retry import (
    MAX_DELAY_SECONDS,
    backoff_delay,
    parse_retry_after,
    urlopen_with_retry,
)


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers: dict[str, str] = (
        {} if retry_after is None else {"Retry-After": retry_after}
    )
    return urllib.error.HTTPError(
        "http://gateway.invalid",
        code,
        "boom",
        headers,
        None,  # type: ignore[arg-type]
    )


def _request() -> urllib.request.Request:
    return urllib.request.Request("http://gateway.invalid", data=b"{}", method="POST")


def test_retries_a_429_then_succeeds_and_honours_retry_after() -> None:
    slept: list[float] = []
    calls: list[int] = []

    def opener(_request: object, timeout: float = 0) -> object:
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(429, retry_after="7")
        return _Response(b'{"ok": true}')

    body = urlopen_with_retry(_request(), timeout=5, opener=opener, sleep=slept.append)

    assert body == b'{"ok": true}'
    assert len(calls) == 2
    # the server's own number wins over our curve
    assert slept == [7.0]


def test_non_retryable_status_is_raised_immediately() -> None:
    slept: list[float] = []
    calls: list[int] = []

    def opener(_request: object, timeout: float = 0) -> object:
        calls.append(1)
        raise _http_error(400)

    with pytest.raises(urllib.error.HTTPError):
        urlopen_with_retry(_request(), timeout=5, opener=opener, sleep=slept.append)

    # retrying a 400 burns quota for nothing
    assert len(calls) == 1
    assert slept == []


def test_a_429_storm_terminates_instead_of_retrying_forever() -> None:
    slept: list[float] = []
    calls: list[int] = []

    def opener(_request: object, timeout: float = 0) -> object:
        calls.append(1)
        raise _http_error(429)

    with pytest.raises(urllib.error.HTTPError):
        urlopen_with_retry(
            _request(), timeout=5, attempts=4, opener=opener, sleep=slept.append
        )

    assert len(calls) == 4
    assert len(slept) == 3


def test_transport_errors_are_retried_then_surface() -> None:
    calls: list[int] = []

    def opener(_request: object, timeout: float = 0) -> object:
        calls.append(1)
        raise urllib.error.URLError("connection reset")

    with pytest.raises(urllib.error.URLError):
        urlopen_with_retry(
            _request(), timeout=5, attempts=3, opener=opener, sleep=lambda _s: None
        )

    assert len(calls) == 3


@pytest.mark.parametrize(
    ("attempt", "ceiling"), [(1, 1.0), (2, 2.0), (3, 4.0), (10, MAX_DELAY_SECONDS)]
)
def test_backoff_is_exponential_jittered_and_capped(
    attempt: int, ceiling: float
) -> None:
    rng = random.Random(0)
    samples = [backoff_delay(attempt, rng=rng) for _ in range(50)]
    assert all(0.0 <= s <= ceiling for s in samples)
    # full jitter, not a fixed curve
    assert len(set(samples)) > 1


@pytest.mark.parametrize(
    ("header", "expected"),
    [(None, None), ("", None), ("3", 3.0), ("-1", 0.0), ("not-a-date", None)],
)
def test_parse_retry_after(header: Any, expected: float | None) -> None:
    assert parse_retry_after(header) == expected


def test_retry_after_is_capped() -> None:
    assert parse_retry_after("100000") == MAX_DELAY_SECONDS
