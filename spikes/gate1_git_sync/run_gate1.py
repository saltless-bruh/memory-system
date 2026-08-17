#!/usr/bin/env python3
"""GATE 1 — Git↔index sync spike (T-0.5 → R-8.4.1).

Question: when files land in the vault via *Git* (not via basic-memory's
own write path) and multiple writers touch it at once, does basic-memory's
file↔DB index stay consistent — and does `basic-memory doctor` detect/repair
drift? Output feeds the SQLite-vs-Postgres decision.

This harness does NOT decide PASS/FAIL for you. It produces an evidence
transcript; a human reads it and records the conclusion in
spikes/GATE_RESULTS.md.

Prereqs (verify first): basic-memory installed, `bm` CLI on PATH or
`python -m basic_memory` importable, project pointed at ../../wiki.

Run:
    python run_gate1.py --project snp-wiki --writers 2 --iterations 20
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WIKI_DIR = REPO_ROOT / "wiki"
OUT_DIR = Path(__file__).resolve().parent / "out"
sys.path.insert(0, str(REPO_ROOT / "spikes" / "_lib"))
import vault  # noqa: E402


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 120) -> dict:
    """Run a command, capture everything, never raise on nonzero."""
    t0 = time.time()
    try:
        p = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return {
            "cmd": " ".join(cmd),
            "rc": p.returncode,
            "stdout": p.stdout[-4000:],
            "stderr": p.stderr[-4000:],
            "secs": round(time.time() - t0, 2),
        }
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {
            "cmd": " ".join(cmd),
            "rc": -1,
            "stdout": "",
            "stderr": f"{type(e).__name__}: {e}",
            "secs": round(time.time() - t0, 2),
        }


def git(*args: str) -> dict:
    return run(["git", *args], cwd=REPO_ROOT)


def bm(*args: str) -> dict:
    """Invoke basic-memory. Tries the `bm`/`basic-memory` CLI, falls back
    to `python -m basic_memory`."""
    for base in (["basic-memory"], ["bm"], [sys.executable, "-m", "basic_memory"]):
        res = run([*base, *args], cwd=REPO_ROOT)
        if not (res["rc"] == -1 and "FileNotFoundError" in res["stderr"]):
            res["_invoked_as"] = " ".join(base)
            return res
    return {
        "cmd": "basic-memory " + " ".join(args),
        "rc": -1,
        "stdout": "",
        "stderr": "basic-memory not found by any method",
        "secs": 0.0,
    }


def writer_commit(worker_id: int, iterations: int) -> list[dict]:
    """One concurrent writer: append an Observation line to log.md and
    commit via Git repeatedly. Concurrent Git writers is exactly the
    contention the gate is probing."""
    events = []
    target = WIKI_DIR / "log.md"
    for i in range(iterations):
        line = (
            f"\n- [spike] gate1 writer={worker_id} iter={i} "
            f"ts={datetime.now(UTC).isoformat()} #spike"
        )
        try:
            with target.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as e:
            events.append({"worker": worker_id, "iter": i, "write_err": str(e)})
            continue
        add = git("add", "wiki/log.md")
        commit = git("commit", "-m", f"spike(gate1): w{worker_id} i{i}", "--no-verify")
        events.append(
            {
                "worker": worker_id,
                "iter": i,
                "add_rc": add["rc"],
                "commit_rc": commit["rc"],
                "commit_err": commit["stderr"][:200] if commit["rc"] else "",
            }
        )
        time.sleep(0.01)
    return events


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--project",
        default="snp-wiki",
        help="basic-memory project name pointed at ../../wiki",
    )
    ap.add_argument("--writers", type=int, default=2)
    ap.add_argument("--iterations", type=int, default=20)
    ap.add_argument(
        "--skip-writers",
        action="store_true",
        help="only run baseline + doctor, no concurrent commits",
    )
    args = ap.parse_args()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    transcript: dict = {"gate": 1, "when": stamp, "args": vars(args), "steps": []}

    def step(name: str, result: dict):
        print(f"\n=== {name} ===")
        print(f"  $ {result.get('cmd', '')}  (rc={result['rc']}, {result['secs']}s)")
        if result["stdout"].strip():
            print("  " + result["stdout"].strip().replace("\n", "\n  ")[:1500])
        if result["rc"] and result["stderr"].strip():
            print("  ERR " + result["stderr"].strip().replace("\n", "\n  ")[:800])
        transcript["steps"].append({"name": name, **result})

    # 0. Preconditions.
    step("git status (baseline)", git("status", "--porcelain"))
    step("basic-memory version", bm("--version"))

    # 1. Ensure basic-memory has synced the vault once (baseline index).
    step("bm sync (baseline)", bm("sync"))
    step("bm status (baseline)", bm("status"))

    # 2. Concurrent Git writers hammering the vault.
    if not args.skip_writers:
        print(f"\n=== concurrent writers: {args.writers} × {args.iterations} ===")
        t0 = time.time()
        with cf.ThreadPoolExecutor(max_workers=args.writers) as ex:
            futures = [
                ex.submit(writer_commit, w, args.iterations)
                for w in range(args.writers)
            ]
            all_events = [ev for f in futures for ev in f.result()]
        failed = [e for e in all_events if e.get("commit_rc") not in (0, None)]
        print(
            f"  {len(all_events)} commit attempts, {len(failed)} nonzero, "
            f"{round(time.time() - t0, 2)}s"
        )
        transcript["steps"].append(
            {
                "name": "concurrent_writers",
                "attempts": len(all_events),
                "failed": len(failed),
                "sample_failures": failed[:5],
            }
        )

    # 3. Re-sync after the storm, then ask doctor about consistency.
    step("bm sync (after writers)", bm("sync"))
    step("bm status (after writers)", bm("status"))
    step("bm doctor", bm("doctor"))

    # 4. Prove index is DERIVED (R-2.5): the git history is intact
    #    regardless of index state.
    step("git log (last 5)", git("log", "--oneline", "-5"))

    out = OUT_DIR / f"gate1_{stamp}.json"
    if vault.write_transcript(out, transcript):
        print(f"\nTranscript → {out.relative_to(REPO_ROOT)}")
    print(
        "NEXT: read the transcript, then record PASS / PASS-with-Postgres /"
        " FAIL in spikes/GATE_RESULTS.md (do not auto-conclude)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
