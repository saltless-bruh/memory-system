"""The one path from a declared command to a `CommandResult`.

Both the CLI dispatcher and the MCP server call this. That is the point: a
command's implementation is located, configured, and run in exactly one place,
so the two surfaces cannot answer differently for the same command.

Two properties must not be re-implemented by callers:

* the implementation is imported **at call time**, so listing or describing
  commands never triggers whatever a command's module does on import — several
  scripts here call `dotenv.load_dotenv()` at module scope;
* configuration is resolved **from the declaration**, so a `NONE` command
  receives nothing and cannot read a credential even by accident.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable
from typing import Any, cast

from scout.cli.config import resolve as resolve_config
from scout.cli.registry import CommandSpec
from scout.cli.result import CommandResult


async def _await_result(result: Awaitable[CommandResult]) -> CommandResult:
    return await result


def invoke(spec: CommandSpec, *args: Any, **kwargs: Any) -> CommandResult:
    """Load, configure and run one declared command."""
    function = spec.load()
    config = resolve_config(spec.prerequisite)
    if "config" in inspect.signature(function).parameters:
        kwargs["config"] = config
    result: object = function(*args, **kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(_await_result(cast(Awaitable[CommandResult], result)))
    return cast(CommandResult, result)


async def ainvoke(spec: CommandSpec, *args: Any, **kwargs: Any) -> CommandResult:
    """Async counterpart used by MCP tools backed by async command logic."""
    function = spec.load()
    config = resolve_config(spec.prerequisite)
    if "config" in inspect.signature(function).parameters:
        kwargs["config"] = config
    result: object = function(*args, **kwargs)
    if inspect.isawaitable(result):
        return await cast(Awaitable[CommandResult], result)
    return cast(CommandResult, result)
