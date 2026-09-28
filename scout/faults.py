"""Which failures belong to one file, and which to the whole indexing cycle.

Both ingestion tiers walk a corpus file by file, and a fault raised inside that
walk has exactly one of two owners:

  * **the cycle** -- a gateway that refused, a database that went away. Every
    later file would fail the same way, so the cycle must stop and the
    supervisor decides whether to wait (`is_transient`) or give up.
  * **the file** -- a byte that is not UTF-8, a symlink where a page should be,
    a scanned PDF with no text layer, text PostgreSQL will not store. Nothing
    about the next file is implied, and waiting cannot fix this one.

Before this split, the second kind was raised out of the walk like the first,
classified permanent, and stopped the watcher for good -- and a restart re-hit
the same file. Calling a *file* fault permanent for the *watcher* is the error;
it is permanent only for that file.

The file-fault set is an allow-list on purpose. An exception of a class nobody
has considered still propagates and fails the cycle loudly: skipping it would
let a pipeline bug that fails every file report a cycle that indexed nothing as
a success.
"""

from __future__ import annotations

import urllib.error
from collections.abc import Mapping, Sequence
from typing import Any

import asyncpg
import httpx

from scout.parsers import ParserError


def is_transient(exc: BaseException) -> bool:
    """Classify only transport/database connectivity failures for retry."""
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, urllib.error.HTTPError):
            return 500 <= current.code < 600 or current.code in {408, 429}
        # The async embed path (`aembed_texts`) raises httpx errors, not urllib
        # ones. Without this branch a 500 from that path is called permanent
        # while the identical 500 from the sync path is called transient.
        if isinstance(current, httpx.HTTPStatusError):
            status = current.response.status_code
            return 500 <= status < 600 or status in {408, 429}
        if isinstance(current, urllib.error.URLError):
            return True
        if isinstance(
            current,
            (
                asyncpg.PostgresConnectionError,
                httpx.NetworkError,
                httpx.TimeoutException,
                TimeoutError,
                ConnectionError,
            ),
        ):
            return True
        current = current.__cause__
    return False


#: Faults a single source can cause by its content alone.
#:
#: `ValueError` covers `UnicodeDecodeError`, the vault walk's refusal of a
#: symlinked or escaping page, and a page with no body (`WikiIngestError`).
#: `ParserError` is a source the parser cannot read without fabricating text.
#: `asyncpg.DataError` is content PostgreSQL rejects at the write -- a NUL byte
#: in `text` -- and it is raised inside the document's own transaction, which
#: has already rolled back by the time it reaches the walk.
FILE_FAULTS: tuple[type[BaseException], ...] = (
    ValueError,
    ParserError,
    asyncpg.DataError,
)


#: Status of a file a cycle could not index because of its own content.
SKIPPED_MALFORMED = "skipped_malformed"


def is_file_fault(exc: BaseException) -> bool:
    """Whether `exc` costs only the file that raised it.

    A transient cause wins over the class it is wrapped in: the vision path
    raises `ParserError` *from* a refused connection, and that is an outage in
    a content fault's clothes. Skipping it would publish a batch that silently
    lost the file while `content_hash` then called it unchanged forever.
    """
    return isinstance(exc, FILE_FAULTS) and not is_transient(exc)


def skipped_file(
    source_uri: str, *, error: str, reason: str, title: str = ""
) -> dict[str, Any]:
    """The result row for a file left out of this cycle, in the pipeline's
    own vocabulary.

    `error` is the exception's class name alone, so it can be logged and
    written to the readiness marker without carrying any of the file's
    content; `reason` keeps the message for a caller that prints results to
    the operator who asked for them.
    """
    return {
        "source_uri": source_uri,
        "title": title,
        "chunks_count": 0,
        "status": SKIPPED_MALFORMED,
        "error": error,
        "reason": reason,
    }


def skipped_summary(results: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    """Content-free `"<source_uri>: <ErrorClass>"` lines for skipped files."""
    return tuple(
        f"{r.get('source_uri')}: {r.get('error')}"
        for r in results
        if r.get("status") == SKIPPED_MALFORMED
    )
