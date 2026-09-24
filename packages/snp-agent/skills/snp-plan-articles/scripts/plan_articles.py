#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Propose a multi-article decomposition of one source, as a receipt.

    scripts/plan_articles.py <source> --dept <dept> [--category concept]
                             [--max-depth 2] [--out plan.json]

Reads the source's own headings. It proposes; it writes no vault page.
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
parser.add_argument("source", help="a file under raw/")
parser.add_argument("--dept", required=True, help="the department to author for")
parser.add_argument("--category", default="concept")
parser.add_argument("--max-depth", type=int, default=2)
parser.add_argument("--out", default=None, help="where to write the plan")
args = parser.parse_args()

argv = [
    args.source,
    "--dept",
    args.dept,
    "--category",
    args.category,
    "--max-depth",
    str(args.max_depth),
]
if args.out:
    argv += ["--out", args.out]
sys.exit(run("plan-articles", argv, {"source": args.source, "dept": args.dept}))
