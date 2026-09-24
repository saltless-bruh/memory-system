#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Ask a running compile batch to stop at its next article boundary.

    scripts/compile_cancel.py <handle>

The batch stops between articles, so work already finished is kept.
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
parser.add_argument("handle", help="the handle `compile_plan` returned")
args = parser.parse_args()
sys.exit(run("compile-cancel", [args.handle], {"handle": args.handle}))
