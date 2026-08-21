"""Progress for a long-running batch compile.

There is no task store here, and that is the point. Step 3 already made
compilation resumable: the plan file lists the articles, and each page is
written into a staging directory the moment it has been generated and judged.
That *is* a durable, inspectable, per-article progress record, so status is
computed by reading it rather than by maintaining a second copy that could
disagree with what is on disk.

The handle is the plan path. A batch is identified by what it compiles, which
means two runs against one plan collide detectably instead of racing.

`.run.json` carries the pid, and a pid that is no longer alive reports
`stalled` rather than `running` — a crashed compile must never look like one
still working.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

RUN_MARKER = ".run.json"


class TaskState(StrEnum):
    """What a batch is doing, judged from the filesystem."""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    #: A run marker exists, but its process is gone and work is unfinished.
    STALLED = "stalled"
    #: Every article is staged; publication may still be pending.
    STAGED = "staged"
    COMPLETE = "complete"


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
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "state": self.state.value,
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


def write_run_marker(plan_path: Path, *, pid: int) -> Path:
    staging = staging_dir(plan_path)
    staging.mkdir(parents=True, exist_ok=True)
    marker = staging / RUN_MARKER
    marker.write_text(
        json.dumps({"pid": pid, "started_at": time.time(), "plan": str(plan_path)}),
        encoding="utf-8",
    )
    return marker


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


def status_for(plan_path: Path, *, wiki_dir: Path | None = None) -> TaskStatus:
    """Compute a batch's progress by reading the plan and its staging directory."""
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
            slug for slug in slugs if next(wiki_dir.rglob(f"{slug}.md"), None) is not None
        ]

    marker = read_run_marker(plan_path)
    pid = int(marker["pid"]) if marker and "pid" in marker else None
    started_at = float(marker["started_at"]) if marker and "started_at" in marker else None

    if slugs and len(published) == len(slugs):
        state = TaskState.COMPLETE
        detail = "every planned page exists in the vault"
    elif marker is not None and pid is not None and process_is_alive(pid):
        state = TaskState.RUNNING
        detail = f"{len(completed)} of {len(slugs)} staged"
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
        extra={"source": source},
    )
