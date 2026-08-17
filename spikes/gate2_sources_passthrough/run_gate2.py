#!/usr/bin/env python3
"""GATE 2 — sources[] passthrough spike (T-0.6 → R-8.4.2, R-1.4).

Question: does basic-memory preserve our custom frontmatter — above all
`sources[]` (the address out to RAG) — byte-for-byte as passthrough when
it indexes a page and when `write_note` rewrites one? Or does it
normalize/strip fields it doesn't recognize?

If it rewrites `sources[]`, the design's fallback (design.md §3) is to
move the address into a marked body block or a `page.sources.yml` sidecar
that basic-memory won't touch. The frontmatter *contract* stays the same;
only the storage location moves.

Method:
  1. Snapshot the exact `sources:` YAML sub-block of a test page.
  2. Let basic-memory sync/index it.
  3. Have basic-memory `write_note` a trivial body change to that page.
  4. Re-read the file from disk; diff the `sources:` block.

Run:
    python run_gate2.py --page wiki/techniques/adcs-esc8.md
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent / "out"
sys.path.insert(0, str(REPO_ROOT / "spikes" / "_lib"))
import vault  # noqa: E402


def bm(*args: str) -> dict:
    for base in (["basic-memory"], ["bm"], [sys.executable, "-m", "basic_memory"]):
        try:
            p = subprocess.run(
                [*base, *args],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired as e:
            return {"rc": -1, "stderr": f"timeout: {e}", "stdout": ""}
        return {
            "rc": p.returncode,
            "stdout": p.stdout,
            "stderr": p.stderr,
            "_as": " ".join(base),
        }
    return {"rc": -1, "stderr": "basic-memory not found", "stdout": ""}


def sources_block(page: vault.Page) -> str:
    """Canonical string form of the sources[] structure for comparison."""
    return json.dumps(page.sources, ensure_ascii=False, indent=2, sort_keys=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--page",
        default="wiki/techniques/adcs-esc8.md",
        help="page with a rich sources[] to probe (2 sources here)",
    )
    args = ap.parse_args()
    page_path = REPO_ROOT / args.page
    if not page_path.exists():
        print(f"FAIL: page not found: {args.page}")
        return 2

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    # 1. Snapshot before.
    before_text = page_path.read_text(encoding="utf-8")
    before = vault.load_page(page_path)
    before_block = sources_block(before)
    print("=== sources[] BEFORE ===")
    print(before_block)

    result = {
        "gate": 2,
        "when": stamp,
        "page": args.page,
        "sources_before": before.sources,
        "steps": [],
    }

    # 2. Let basic-memory index it.
    r = bm("sync")
    result["steps"].append({"name": "bm sync", **r})
    print(f"\nbm sync rc={r['rc']} ({r.get('_as', '?')})")
    if r["rc"] != 0:
        print("  NOTE: basic-memory not runnable here — this harness needs it.")
        print("  " + r["stderr"][:400])

    # 3. Force a write_note round-trip. basic-memory addresses notes by
    #    title/permalink; we append a harmless observation line so the
    #    engine rewrites the file through its own serializer.
    #    (Exact write_note invocation depends on installed CLI surface;
    #    try a couple of shapes and record which worked.)
    title = before.frontmatter.get("title", before.slug)
    probe = f"gate2 passthrough probe {stamp}"
    write_attempts = [
        [
            "tool",
            "write-note",
            "--title",
            title,
            "--folder",
            "techniques",
            "--content",
            probe,
        ],
        ["write-note", "--title", title, "--content", probe],
    ]
    wrote = None
    for attempt in write_attempts:
        r = bm(*attempt)
        result["steps"].append({"name": "write_note attempt", "argv": attempt, **r})
        print(f"\nwrite_note {' '.join(attempt[:2])} → rc={r['rc']}")
        if r["rc"] == 0:
            wrote = attempt
            break
    result["write_note_used"] = wrote

    # 4. Re-read from disk and diff the sources block.
    after = vault.load_page(page_path)
    after_block = sources_block(after)
    after_text = page_path.read_text(encoding="utf-8")

    identical = before_block == after_block
    result["sources_after"] = after.sources
    result["sources_identical"] = identical

    print("\n=== sources[] AFTER ===")
    print(after_block)
    print(f"\n=== VERDICT: sources[] {'PRESERVED' if identical else 'CHANGED'} ===")
    if not identical:
        diff = "\n".join(
            difflib.unified_diff(
                before_block.splitlines(),
                after_block.splitlines(),
                "before",
                "after",
                lineterm="",
            )
        )
        print(diff)
        result["sources_diff"] = diff
        print(
            "\n→ Gate 2 FAILS passthrough. Activate body-block/sidecar "
            "fallback (design.md §3). Contract unchanged; storage moves."
        )
    else:
        print(
            "\n→ Gate 2 PASS candidate: sources[] survived one round-trip. "
            "Confirm across a few more edits before recording PASS."
        )

    # Full-file diff too, for the human reviewer.
    if before_text != after_text:
        result["file_changed"] = True

    out = OUT_DIR / f"gate2_{stamp}.json"
    if vault.write_transcript(out, result):
        print(f"\nTranscript → {out.relative_to(REPO_ROOT)}")
    return 0 if result.get("sources_identical") else 1


if __name__ == "__main__":
    raise SystemExit(main())
