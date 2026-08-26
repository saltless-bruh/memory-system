# Ideas: adding a file in agent package that showcase what are the cli commands, definition and example of correct usecase.
Worth doing — but not as a hand-written list, and probably not in `workflows/`.

**Why the need is real:** an agent connected to this package currently has no way to learn the CLI surface. `manifest.json` advertises only `basic-memory` and `scout`; nothing mentions `snpmemory` at all. So yes, something has to tell an agent what the commands are.

**Why a hand-maintained file is the wrong shape:** the repo already has the evidence. `packages/snp-agent/` is 25 files behind `.agent/` — pure drift, zero content differences, nobody noticed. A CLI list would drift worse, because the surface is actively changing: 10 of 24 specified commands exist today, and 14 are still to be written. A stale list is worse than no list, since an agent will confidently call a command that doesn't exist or pass a flag that was renamed.

**The shape I'd recommend — split facts from judgment:**

- **Facts → generated.** `snpmemory schema` already exists precisely for this: machine-readable description of every command, its output format, exit codes, and error kinds, and it runs with no auth, no config, no network. Generate the reference file from the registry (`scout/cli/declarations.py`) at package-build time, the same pattern as the MCP server, which is already "generated from the registry" rather than hand-copied. Then add a test asserting the generated file matches `snpmemory schema` output, so drift fails CI instead of shipping.
- **Judgment → hand-written, and kept small.** The things `schema` genuinely can't express are worth writing by hand: read the wiki before descending to `rag_fetch`; stop when the page answers the question (R-5.1); never hand-write a `hint`, mint it (R-6.3); `propose`, never commit to main (R-6.4/R-7.3); and what the exit codes *mean* in practice — 1 is a real finding, 2 is infrastructure and must never trigger a mutation. That's maybe 30 lines and it changes rarely.

**On placement:** if `workflows/` holds procedures ("do these steps in this order") and `instructions/` holds standing reference, a command list belongs in `instructions/`. A workflow file makes sense only for something like "how to author a page end to end" — which would then *cite* the generated reference rather than restate it.

**One caution on examples:** correct-usage examples are the highest-value and highest-rot part. Every example you write for a command that doesn't exist yet (`mint`, `compile`, `propose`, `heal`, `gate`) is a guess that will be wrong in detail. I'd write examples only for the ten commands that actually run today, and let the generator add the rest as they land.

So: good idea, and it closes a real gap — just make the volatile half generated and keep the hand-written half to the rules an agent can't infer from a schema.