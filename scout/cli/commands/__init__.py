"""Command implementations.

Each module defines plain functions returning `CommandResult`. Heavy imports —
database drivers, `dotenv`, anything reading credentials — happen **inside** the
function body, never at module scope. `snpmemory schema` and `--help` must
answer on a machine with no configuration and no running stack, and a module
that loads an environment when imported would quietly break that.
"""
