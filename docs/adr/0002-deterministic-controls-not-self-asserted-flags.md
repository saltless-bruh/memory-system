# ADR-0002 — A self-asserted flag must not be a security boundary

- **Status:** Accepted · 2026-09-21
- **Deciders:** repository owner

## Context

The governance gate's rule was derived from each command's own declaration:
*destructive is never exposed; a write command may be exposed only behind an
explicit `--confirm`.* That replaced a hardcoded name list which had gone stale
(it required two retired commands, `gate` and `heal`, to be hidden).

But `init` — which generates and rotates credentials — **declares `--confirm`**,
so the derived rule alone would have licensed exposing it.

MCP's own guidance is explicit that declared properties are not enforcement:

> "An untrusted server can claim `readOnlyHint: true` and delete your files
> anyway… They aren't enforcement." — and hosts should "keep your actual safety
> guarantees in deterministic controls."

## Decision

Two layers, with the boundary in the deterministic one:

```
  SELF-ASSERTED   declared effect, --confirm   -> routes confirmation prompts
  ────────────────────────────────────────────────────────────────────────────
  DETERMINISTIC   never-expose list, absent    -> decides what is possible
                  capability, RLS, network
```

`init` joins `propose`, `ingest` and `ingest-wiki` on the never-expose list.
`--confirm` remains a second layer, not the fence.

## Consequences

- **Worse:** the never-expose list is a hardcoded list again, which is what went
  stale before. Mitigated: every name on it is asserted to be a declared command,
  so a retirement fails loudly.
- **Better:** credential generation cannot be exposed by a command declaring a
  flag about itself.
- **Proven, not assumed:** `leaf-1.2:G3` plant E exposes `init` and requires the
  gate to catch it **by name**, so if the list is ever the only thing holding
  this, that is visible.
