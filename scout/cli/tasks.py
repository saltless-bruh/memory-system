"""Progress for a long-running batch compile.

There is no task store here, and that is the point. Step 3 already made
compilation resumable: the plan file lists the articles, and each page is
written into a staging directory the moment it has been generated and judged.
That *is* a durable, inspectable, per-article progress record, so status is
computed by reading it rather than by maintaining a second copy that could
disagree with what is on disk.

The handle is the plan path, **resolved and absolute**. A batch is identified by
what it compiles, which means two runs against one plan collide detectably
instead of racing — but only if the two runs agree on what the path names. A
relative handle names a different file from a different directory, so an agent
that started a batch in one place and polled it from another was told
`not_started` for a run happily in progress, which is the one answer that
invites starting it a second time.

Resolving against a **pinned root** rather than the process cwd is what fixes
that, and the same rule bounds where a tool call can write: the staging
directory is derived from the plan path, so a plan path that escapes the root
would take the staging directory out with it.

`.run.json` carries the pid, and a pid that is no longer alive reports
`stalled` rather than `running` — a crashed compile must never look like one
still working.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from scout.cli.errors import input_error

RUN_MARKER = ".run.json"

#: A file beside the run marker asking the batch to stop. Cancellation is
#: cooperative by design: the batch reads this between articles, where the
#: staging directory is already consistent, and stops there. Killing the process
#: instead would leave a half-generated article and a marker claiming `running`.
CANCEL_MARKER = ".cancel"

#: Suggested polling cadence, in seconds. Deliberately the same value fastmcp's
#: `TaskConfig` defaults to, so a task-capable client and one that polls this
#: handle are told the same thing rather than two different things.
POLL_INTERVAL_SECONDS = 5.0

#: How long a batch may go without finishing an article before its `running`
#: claim stops being trusted. Generous on purpose: one article costs two
#: generations and a judge, so minutes are normal, and calling a working batch
#: stalled is worse than calling a hung one stalled late.
HEARTBEAT_TTL_SECONDS = 900.0

#: Fields of a planned article that change what its page would say. `section`
#: is not among them — it is a display ordinal and never reaches generation, so
#: renumbering a plan must not invalidate a batch that is halfway through it.
_MATERIAL_ARTICLE_FIELDS = ("slug", "title", "loc", "category", "department", "links")


class TaskState(StrEnum):
    """What a batch is doing, judged from the filesystem."""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    #: A run marker exists, but its process is gone and work is unfinished.
    STALLED = "stalled"
    #: Every article is staged; publication may still be pending.
    STAGED = "staged"
    COMPLETE = "complete"
    #: The run ended by itself and did not finish. Terminal.
    FAILED = "failed"
    #: The run was asked to stop and did. Terminal.
    CANCELLED = "cancelled"


#: States a batch does not leave on its own. A caller may stop polling on these.
TERMINAL_STATES = frozenset({TaskState.COMPLETE, TaskState.FAILED, TaskState.CANCELLED})


@dataclass(frozen=True, slots=True)
class TaskStatus:
    """One batch's progress, derived entirely from disk."""

    handle: str
    state: TaskState
    total: int
    done: int
    pending: tuple[str, ...] = ()
    completed: tuple[str, ...] = ()
    published: tuple[str, ...] = ()
    pid: int | None = None
    started_at: float | None = None
    detail: str = ""
    #: When the batch last finished an article. `None` for a run started before
    #: heartbeats existed, which is why staleness is only asserted when it is set.
    last_heartbeat: float | None = None
    #: How often a caller should poll, and how long a `running` claim is good
    #: for. Reported so a client is told rather than left to guess.
    poll_interval: float = POLL_INTERVAL_SECONDS
    ttl: float = HEARTBEAT_TTL_SECONDS
    exit_code: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "state": self.state.value,
            "terminal": self.is_terminal,
            "poll_interval": self.poll_interval,
            "ttl": self.ttl,
            "last_heartbeat": self.last_heartbeat,
            "exit_code": self.exit_code,
            "total": self.total,
            "done": self.done,
            "pending": list(self.pending),
            "completed": list(self.completed),
            "published": list(self.published),
            "pid": self.pid,
            "started_at": self.started_at,
            "detail": self.detail,
            **self.extra,
        }


def resolve_plan_path(plan_path: Path, root: Path) -> Path:
    """Resolve a plan path and refuse anything that lands outside `root`.

    A relative path is resolved the way a shell resolves it — against the
    process cwd — and `root` is a **boundary**, not an anchor. Anchoring to the
    root instead was tried and is worse in both directions: `cd docs &&
    ... ../artifacts/plan.json` would be refused with "pass a path under
    <root>", which is untrue of a path that plainly is under it, while
    `cd docs && ... plan.json` would silently operate on `<root>/plan.json`
    rather than the file the caller was looking at.

    What actually makes a handle mean the same thing everywhere is that the
    handle handed back is **absolute** (see `status_for`), so a caller who
    stores one and returns to it later names the same batch. This function's job
    is the other half: keeping the batch inside the checkout. The staging
    directory is derived from the plan path, so an escaping plan path would take
    everything the batch writes out with it.

    Resolution happens before the comparison, so `../../etc/plan.json` and a
    symlink pointing out of the tree are both caught rather than only a literal
    `..`. The refusal is input validation (exit 3), and it is deliberately not
    conditional on the file existing: a caller must not learn what is outside
    the root by watching which error it gets.
    """
    root = root.resolve()
    resolved = plan_path.resolve()
    if resolved == root or root not in resolved.parents:
        raise input_error(
            f"{plan_path} resolves outside this checkout",
            hint=f"a plan must live under {root}; it resolved to {resolved}",
            root=str(root),
            resolved=str(resolved),
        )
    return resolved


def staging_dir(plan_path: Path) -> Path:
    """Where a batch stages pages. Must match `scripts/compile_plan.py`."""
    return plan_path.with_suffix(".staging")


def process_is_alive(pid: int) -> bool:
    """True when `pid` names a live process on this machine."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive, owned by somebody else.
        return True
    return True


def _digest(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def plan_fingerprints(plan_path: Path) -> tuple[str, dict[str, str]]:
    """Fingerprint a plan's *content*, overall and per article.

    Hashes the parsed article list rather than the file's bytes, so reformatting
    a plan, reordering its keys, or renumbering its sections does not invalidate
    a batch that is halfway through compiling it. What is hashed is exactly what
    reaches generation — see `_MATERIAL_ARTICLE_FIELDS`.

    Returns:
        `("", {})` when the plan cannot be read. A fingerprint that cannot be
        computed must never be reported as one that matches.
    """
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", {}
    if not isinstance(payload, dict):
        return "", {}

    per_article: dict[str, str] = {}
    canonical: list[dict[str, Any]] = []
    for entry in payload.get("articles", []):
        if not isinstance(entry, dict) or "slug" not in entry:
            continue
        material = {name: entry.get(name) for name in _MATERIAL_ARTICLE_FIELDS}
        canonical.append(material)
        per_article[str(entry["slug"])] = _digest(material)

    overall = _digest({"source": payload.get("source", ""), "articles": canonical})
    return overall, per_article


def describe_plan_drift(recorded: dict[str, str], current: dict[str, str]) -> str:
    """Name what changed between two per-article fingerprint maps.

    A refusal that says only "mismatch" leaves the operator to diff the plan by
    hand against a version they no longer have.
    """
    changed = sorted(
        slug
        for slug, digest in current.items()
        if slug in recorded and recorded[slug] != digest
    )
    added = sorted(set(current) - set(recorded))
    removed = sorted(set(recorded) - set(current))
    parts = []
    if changed:
        parts.append(f"{len(changed)} changed ({', '.join(changed)})")
    if added:
        parts.append(f"{len(added)} added ({', '.join(added)})")
    if removed:
        parts.append(f"{len(removed)} removed ({', '.join(removed)})")
    return "; ".join(parts) or "the plan's source path changed"


def write_run_marker(
    plan_path: Path,
    *,
    pid: int,
    fingerprint: str = "",
    article_fingerprints: dict[str, str] | None = None,
) -> Path:
    """Record who is working on this batch, and on which version of the plan."""
    staging = staging_dir(plan_path)
    staging.mkdir(parents=True, exist_ok=True)
    marker = staging / RUN_MARKER
    now = time.time()
    marker.write_text(
        json.dumps(
            {
                "pid": pid,
                "started_at": now,
                "plan": str(plan_path),
                "state": TaskState.RUNNING.value,
                "plan_fingerprint": fingerprint,
                "article_fingerprints": article_fingerprints or {},
                "poll_interval": POLL_INTERVAL_SECONDS,
                "ttl": HEARTBEAT_TTL_SECONDS,
                "last_heartbeat": now,
            }
        ),
        encoding="utf-8",
    )
    return marker


def _update_run_marker(plan_path: Path, changes: dict[str, Any]) -> bool:
    """Merge `changes` into an existing marker. False when there is none."""
    marker = staging_dir(plan_path) / RUN_MARKER
    payload = read_run_marker(plan_path)
    if payload is None:
        return False
    payload.update(changes)
    marker.write_text(json.dumps(payload), encoding="utf-8")
    return True


def heartbeat(plan_path: Path) -> bool:
    """Record that the batch is still making progress.

    A liveness check on the pid alone cannot tell a working process from a hung
    one, and pids are reused. One small write per article buys a `running` claim
    that expires.
    """
    return _update_run_marker(plan_path, {"last_heartbeat": time.time()})


def finish_run(
    plan_path: Path,
    *,
    state: TaskState,
    detail: str = "",
    exit_code: int | None = None,
) -> bool:
    """Record a terminal outcome so a dead batch stops reading as `stalled`.

    Only meaningful for the outcomes that leave the staging directory behind. A
    successful publish removes staging entirely, and the vault is then the
    evidence.
    """
    return _update_run_marker(
        plan_path,
        {
            "state": state.value,
            "detail": detail,
            "exit_code": exit_code,
            "finished_at": time.time(),
        },
    )


def request_cancel(plan_path: Path) -> Path:
    """Ask a running batch to stop at its next article boundary.

    Writing a file rather than signalling a pid: a signal arrives mid-article and
    would abandon a generation that has already been paid for, and a pid may
    have been reused by something else entirely.
    """
    staging = staging_dir(plan_path)
    staging.mkdir(parents=True, exist_ok=True)
    marker = staging / CANCEL_MARKER
    marker.write_text(json.dumps({"requested_at": time.time()}), encoding="utf-8")
    return marker


def cancel_requested(plan_path: Path) -> bool:
    return (staging_dir(plan_path) / CANCEL_MARKER).is_file()


def clear_cancel(plan_path: Path) -> None:
    """Consume a cancellation request, so a later re-run is not killed by it."""
    (staging_dir(plan_path) / CANCEL_MARKER).unlink(missing_ok=True)


def read_run_marker(plan_path: Path) -> dict[str, Any] | None:
    marker = staging_dir(plan_path) / RUN_MARKER
    if not marker.is_file():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _planned_slugs(plan_path: Path) -> tuple[list[str], str]:
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    articles = payload.get("articles", [])
    slugs = [str(a["slug"]) for a in articles if isinstance(a, dict) and "slug" in a]
    return slugs, str(payload.get("source", ""))


def status_for(
    plan_path: Path, *, wiki_dir: Path | None = None, root: Path | None = None
) -> TaskStatus:
    """Compute a batch's progress by reading the plan and its staging directory.

    Args:
        plan_path: The handle. Relative paths are anchored to `root`.
        wiki_dir: Where published pages land, so a finished batch reads
            `complete` after its staging directory has been removed.
        root: The pinned checkout. Supplying it is what makes a handle mean the
            same thing from any directory, and what refuses a path that escapes.

    Raises:
        CliError: `plan_path` resolves outside `root`.
    """
    plan_path = (
        resolve_plan_path(plan_path, root) if root is not None else plan_path.resolve()
    )
    # Absolute, always: a handle a caller stores and uses later has to name the
    # same file when it does.
    handle = plan_path.as_posix()
    if not plan_path.is_file():
        return TaskStatus(
            handle=handle,
            state=TaskState.NOT_STARTED,
            total=0,
            done=0,
            detail=f"no plan at {handle}",
        )

    try:
        slugs, source = _planned_slugs(plan_path)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        return TaskStatus(
            handle=handle,
            state=TaskState.NOT_STARTED,
            total=0,
            done=0,
            detail=f"plan could not be read: {exc}",
        )

    staging = staging_dir(plan_path)
    completed = [slug for slug in slugs if (staging / f"{slug}.md").is_file()]
    pending = [slug for slug in slugs if slug not in completed]

    # A successful publish removes the staging directory, so "no staging and
    # pages on disk" is completion rather than never having started.
    published: list[str] = []
    if wiki_dir is not None and wiki_dir.is_dir():
        published = [
            slug
            for slug in slugs
            if next(wiki_dir.rglob(f"{slug}.md"), None) is not None
        ]

    marker = read_run_marker(plan_path)
    pid = int(marker["pid"]) if marker and "pid" in marker else None
    started_at = (
        float(marker["started_at"]) if marker and "started_at" in marker else None
    )
    poll_interval = float((marker or {}).get("poll_interval") or POLL_INTERVAL_SECONDS)
    ttl = float((marker or {}).get("ttl") or HEARTBEAT_TTL_SECONDS)
    last_heartbeat = (marker or {}).get("last_heartbeat")
    last_heartbeat = float(last_heartbeat) if last_heartbeat is not None else None
    exit_code = (marker or {}).get("exit_code")
    exit_code = int(exit_code) if isinstance(exit_code, int) else None
    recorded = (marker or {}).get("state")
    terminal = next(
        (s for s in (TaskState.FAILED, TaskState.CANCELLED) if s.value == recorded),
        None,
    )
    # A `running` claim expires. A live pid says a process exists, not that it is
    # working, and pids are reused — so once a batch has been quiet for longer
    # than its TTL, say so rather than repeating a claim nothing supports.
    silent_for = time.time() - last_heartbeat if last_heartbeat is not None else None
    heartbeat_is_stale = silent_for is not None and silent_for > ttl

    if slugs and len(published) == len(slugs):
        state = TaskState.COMPLETE
        detail = "every planned page exists in the vault"
    elif terminal is not None:
        # The run said what happened to it. That beats anything inferred from a
        # pid, and it is why a dead batch stops reading as `stalled` forever.
        state = terminal
        recorded_detail = str((marker or {}).get("detail") or "")
        detail = recorded_detail or f"the run ended as {terminal.value}"
        if pending:
            detail += f"; {len(pending)} article(s) unfinished — re-run to resume"
    elif (
        marker is not None
        and pid is not None
        and process_is_alive(pid)
        and not heartbeat_is_stale
    ):
        state = TaskState.RUNNING
        detail = f"{len(completed)} of {len(slugs)} staged"
    elif marker is not None and pid is not None and process_is_alive(pid):
        state = TaskState.STALLED
        detail = (
            f"process {pid} is alive but has finished no article in "
            f"{int(silent_for or 0)}s (ttl {int(ttl)}s) — it may be hung"
        )
    elif marker is not None and pending:
        state = TaskState.STALLED
        detail = (
            f"a run marker exists but process {pid} is gone with "
            f"{len(pending)} article(s) unfinished — re-run to resume from staging"
        )
    elif slugs and not pending and completed:
        state = TaskState.STAGED
        detail = "every article is staged; publication has not completed"
    elif completed:
        state = TaskState.STALLED
        detail = f"{len(completed)} of {len(slugs)} staged, no run in progress"
    else:
        state = TaskState.NOT_STARTED
        detail = "nothing staged yet"

    # `done` must mean "articles finished", not "articles still in staging".
    # A successful publish deletes the staging directory, so counting staged
    # files alone reports `complete: 0/3` — which reads as nothing happened.
    done = len(published) if state is TaskState.COMPLETE else len(completed)

    return TaskStatus(
        handle=handle,
        state=state,
        total=len(slugs),
        done=done,
        pending=tuple(pending),
        completed=tuple(completed),
        published=tuple(published),
        pid=pid,
        started_at=started_at,
        detail=detail,
        last_heartbeat=last_heartbeat,
        poll_interval=poll_interval,
        ttl=ttl,
        exit_code=exit_code,
        extra={"source": source},
    )
