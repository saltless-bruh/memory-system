"""`snpmemory ingest` — index `raw/` into pgvector on demand.

The `sync-job` container already does this on a file watch (Nhịp A). This is the
same pipeline driven by hand, for the cases a watch does not cover: a first
load, a re-index after a parser change, a machine where the stack runs but the
watcher does not.

Two rules it does not bend:

**Nothing outside `raw/` is indexed (R-3.1).** The corpus root is the boundary,
and a path outside it is a caller error, not something to resolve helpfully.

**No implicit department.** `ingest_directory` requires exactly one ACL
authority; the checked-in `.acl.yaml` decides each file, and a file matching no
rule is not indexed at all. Nothing becomes publicly readable by omission.

Every heavy import happens inside a function. Importing this module must not
read an environment, open a database, or resolve a credential.
"""

from __future__ import annotations

from typing import Annotated, Any

from cyclopts import Parameter

from scout.cli.config import Config
from scout.cli.errors import CliError, conflict_error, infrastructure_error, input_error
from scout.cli.result import CommandResult, ErrorKind, ExitCode

#: Injected by the dispatcher; never a user-facing flag.
Injected = Annotated[Any, Parameter(parse=False)]


def _under_raw(candidate: Any, raw_root: Any) -> Any:
    """Resolve `candidate` and refuse anything that escapes `raw_root`.

    Resolution happens before the comparison so `raw/../etc/passwd` and a symlink
    pointing out of the tree are both caught, rather than only a literal `..`.
    """
    resolved = candidate.resolve()
    root = raw_root.resolve()
    if resolved != root and root not in resolved.parents:
        raise input_error(
            f"{candidate} is outside the corpus root",
            hint=f"pass a path under {root}",
        )
    if not resolved.exists():
        raise input_error(f"{candidate} does not exist")
    return resolved


def ingest(
    *,
    path: str | None = None,
    dir: str | None = None,  # noqa: A002 - the specified flag name
    dry_run: bool = False,
    confirm: bool = False,
    allow_capability_change: bool = False,
    acknowledge_capability_change: str | None = None,
    actor_hint: str | None = None,
    config: Injected = None,
) -> CommandResult:
    """Index one file, or a directory, into pgvector under the corpus ACL map."""
    import asyncio
    from pathlib import Path

    from scout.ingest import (
        AclPolicyError,
        CapabilityMismatchError,
        DocumentAclMap,
        ingest_directory,
    )

    cfg: Config = config
    repo = cfg.require_repo()

    if (path is None) == (dir is None):
        raise input_error(
            "pass exactly one of --path or --dir",
            hint="--path indexes one file; --dir indexes a tree",
        )

    raw_root = Path(cfg.get("RAW_DIR") or (repo / "raw"))
    if not raw_root.is_absolute():
        raw_root = repo / raw_root

    # Anchor a relative argument against the checkout rather than the process
    # cwd. `--dir raw/papers` names one tree, and it has to name that same tree
    # from a subdirectory as from the repository root; resolving it against the
    # shell's cwd made the command refuse its own corpus from anywhere else.
    requested = Path(path or dir or "")
    if not requested.is_absolute():
        requested = repo / requested
    target = _under_raw(requested, raw_root)
    if path is not None and target.is_dir():
        raise input_error("--path names a directory", hint="use --dir instead")
    if dir is not None and not target.is_dir():
        raise input_error("--dir names a file", hint="use --path instead")

    if not dry_run and not confirm:
        raise CliError(
            ErrorKind.CONFIRMATION_REQUIRED,
            f"would index {target.relative_to(repo).as_posix()} into pgvector — not performed",
            hint=(
                "indexing spends embedding calls and replaces this source's rows; "
                "see the plan with --dry-run, then re-run with --confirm"
            ),
            details={"target": target.relative_to(repo).as_posix()},
        )

    acl_file = cfg.get("RAW_ACL_FILE")
    try:
        acl = DocumentAclMap.from_file(
            Path(acl_file) if acl_file else raw_root / ".acl.yaml", base_dir=raw_root
        )
    except AclPolicyError as exc:
        # Without a readable policy there is no authority to publish anything.
        # Defaulting here is how a private document becomes world-readable.
        raise conflict_error(
            "the document ACL map could not be read",
            hint="fix raw/.acl.yaml; nothing is indexed without a policy",
            cause=str(exc),
        ) from exc

    async def run() -> list[dict[str, Any]]:
        return await ingest_directory(
            dir_path=target if target.is_dir() else target.parent,
            acl=acl,
            dry_run=dry_run,
            reconcile=target.is_dir(),
            allow_capability_change=allow_capability_change,
            acknowledge_capability_change=acknowledge_capability_change,
            actor_hint=actor_hint,
            # The dispatcher already resolved the project `.env` for this
            # command's prerequisite class. Letting the connection path read
            # `os.environ` instead threw that away.
            env=cfg.values,
        )

    try:
        results = asyncio.run(run())
    except CliError:
        raise
    except CapabilityMismatchError as exc:
        # Exit 7: a conflict with what is already there, not a malformed request
        # and not an outage. Nothing was written and nothing was deleted.
        raise conflict_error(
            "this environment would build a different corpus than the one on disk",
            hint=(
                "re-ingest through the image that built it, or acknowledge this "
                "exact difference: --allow-capability-change together with "
                "--acknowledge-capability-change='<the difference printed below>'. "
                "The override is recorded in the append-only ingest_events table."
            ),
            cause=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001 - a driver trace may carry a DSN
        raise infrastructure_error(
            "ingestion could not complete",
            hint="indexing needs postgres AND the embedding route",
            retryable=True,
            cause=type(exc).__name__,
        ) from exc

    if not target.is_dir():
        wanted = target.relative_to(raw_root.parent).as_posix()
        results = [row for row in results if row.get("source_uri") == wanted]

    indexed = [row for row in results if row.get("chunks_count")]
    empty = [row for row in results if not row.get("chunks_count")]
    return CommandResult(
        exit_code=ExitCode.SEMANTIC_FAILURE
        if empty and not indexed
        else ExitCode.SUCCESS,
        data={
            "status": "dry_run" if dry_run else "indexed",
            "documents": results,
            "indexed": len(indexed),
            "no_evidence": [row.get("source_uri") for row in empty],
        },
        summary=(
            f"{'[dry-run] would index' if dry_run else 'indexed'} "
            f"{len(indexed)} document(s)"
            + (f"; {len(empty)} yielded no text" if empty else "")
        ),
    )
