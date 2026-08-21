"""Every command this tool offers, declared in one place.

These live apart from the dispatcher because they have more than one consumer:
`snpmemory` registers them, `snpmemory schema` describes them, the MCP exposure
policy decides how each is surfaced, and the MCP server builds tools from them.
Declaring them inside the dispatcher would make an empty registry the default
for everyone who did not happen to import it, which is precisely the drift
`registry.py` was written to prevent.

Importing this module registers declarations only. No implementation is
imported until a command actually runs — see `scout/cli/invoke.py`.
"""

from __future__ import annotations

from scout.cli.registry import REGISTRY, CommandSpec, Effect, Prerequisite, command
from scout.cli.result import ErrorKind, ExitCode

# Commands are declared as they are implemented. `schema` therefore reports what
# actually exists rather than what is planned, which is the only way an agent
# can trust it.

command(
    "schema",
    "Describe every command, output format, exit code, and error kind.",
    "scout.cli.commands.schema:schema",
    prerequisite=Prerequisite.NONE,
)

_SEMANTIC = (ExitCode.SEMANTIC_FAILURE,)

command(
    "verify-vault",
    "Lint page frontmatter and confirm wiki/index.md is current.",
    "scout.cli.commands.verify:verify_vault",
    outcomes=_SEMANTIC,
    errors=(ErrorKind.INPUT_VALIDATION, ErrorKind.INFRASTRUCTURE),
)
command(
    "verify-secrets",
    "Scan tracked, staged, and untracked bytes for credential-shaped values.",
    "scout.cli.commands.verify:verify_secrets",
    outcomes=_SEMANTIC,
    errors=(ErrorKind.INPUT_VALIDATION,),
)
command(
    "verify-addresses",
    "Check that every page's sources[] hint still retrieves its own file.",
    "scout.cli.commands.verify:verify_addresses",
    outcomes=_SEMANTIC,
    errors=(ErrorKind.INPUT_VALIDATION, ErrorKind.INFRASTRUCTURE),
)
command(
    "verify-groundedness",
    "Judge each page's body against the sources it cites.",
    "scout.cli.commands.verify:verify_groundedness",
    outcomes=_SEMANTIC,
    errors=(ErrorKind.INPUT_VALIDATION, ErrorKind.INFRASTRUCTURE),
)
command(
    "plan-articles",
    "Propose a multi-article decomposition from a source's own headings.",
    "scout.cli.commands.compile:plan_articles",
    outcomes=_SEMANTIC,
    errors=(ErrorKind.INPUT_VALIDATION, ErrorKind.INFRASTRUCTURE),
)
command(
    "compile-plan",
    "Compile every article in an approved plan; writes nothing unless all pass.",
    "scout.cli.commands.compile:compile_plan",
    effect=Effect.WRITE,
    outcomes=_SEMANTIC,
    errors=(ErrorKind.INPUT_VALIDATION, ErrorKind.INFRASTRUCTURE),
)
command(
    "mcp",
    "Serve these operations to an MCP agent over stdio.",
    "scout.cli.commands.mcp:mcp",
    errors=(ErrorKind.INPUT_VALIDATION, ErrorKind.INFRASTRUCTURE),
)
command(
    "compile-status",
    "Report a background batch's progress from its plan and staging directory.",
    "scout.cli.commands.compile:compile_status",
    outcomes=_SEMANTIC,
    errors=(ErrorKind.INPUT_VALIDATION, ErrorKind.INFRASTRUCTURE),
)
command(
    "check",
    "Run every verification in order and stop at the first failure.",
    "scout.cli.commands.verify:check",
    outcomes=_SEMANTIC,
    errors=(ErrorKind.INPUT_VALIDATION, ErrorKind.INFRASTRUCTURE),
)


#: Every declared command, in name order.
#:
#: Importing this module is what puts the declarations on `REGISTRY`. Exporting
#: them as a value as well means importers depend on something concrete rather
#: than on an import side effect that a formatter is free to delete — which is
#: exactly how the dispatcher briefly lost every command.
DECLARED: tuple[CommandSpec, ...] = tuple(REGISTRY)
