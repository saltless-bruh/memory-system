#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Compile every article in a plan file, or rehearse it.

    scripts/compile_plan.py <plan.json> [--dry-run] [--confirm] [--background]

Writing is approved per call, never at install time: without `--confirm` the
command refuses to mutate, and `--dry-run` reports what it would do. Nothing
here pushes; the result is reviewed on a branch (R-6.4, R-7.3).
"""

from __future__ import annotations

import argparse
import pathlib
import sys

# Importing the sibling runner would otherwise write `__pycache__/` into the
# shipped skill, and a skill that litters the package it ships in is a skill
# that fails its own package-sync check.
sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _runner import run  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("plan", help="the plan file to compile")
parser.add_argument("--confirm", action="store_true", help="approve the write")
parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
parser.add_argument("--background", action="store_true", help="return a handle")
args = parser.parse_args()

argv = [args.plan]
for flag in ("confirm", "dry_run", "background"):
    if getattr(args, flag):
        argv.append("--" + flag.replace("_", "-"))
sys.exit(
    run(
        "compile-plan",
        argv,
        {"plan": args.plan, "confirm": args.confirm, "dryRun": args.dry_run},
    )
)
