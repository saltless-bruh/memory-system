#!/usr/bin/env python3
"""`extract_references` — print a document's reference list as JSON.

A thin CLI over `scout.references`, which holds the parsing. See that module for
why the reference list is extracted at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scout.references import parse_references  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Source document beneath raw/.")
    args = parser.parse_args(argv)

    from scout.parsers import parse_file

    document = parse_file(Path(args.path), base_dir=REPO_ROOT)
    references = parse_references(document.full_text)
    untitled = sum(1 for r in references if r.title is None)

    print(
        json.dumps(
            {
                "source": args.path,
                "count": len(references),
                "unparsed_titles": untitled,
                "references": [r.to_dict() for r in references],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
