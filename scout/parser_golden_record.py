"""Record a parser golden for the environment this process is running in.

    uv run python -m scout.parser_golden_record

Deliberately a separate entry point from the test. Re-recording is how a
legitimate parser change is accepted, and it must be an explicit act — a test
that silently rewrote its own expectation when it failed would assert nothing.

Records only for the *current* capability environment. Goldens for an
environment you are not in cannot be produced honestly, so they are not.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from scout.capabilities import capability_fingerprint
from scout.parser_golden import golden_key, golden_path, parse_digest
from scout.parsers import parse_pdf

SOURCE_URI = "raw/papers/computers-12-00091.pdf"

_COMMENT = (
    "Golden digest of the PARSED STRUCTURE of the corpus document, for one "
    "capability environment. Re-record with `uv run python -m "
    "scout.parser_golden_record`. A mismatch means the parser produces "
    "different output than when this was recorded: either the change was "
    "deliberate, in which case bump PARSER_REVISION in scout/capabilities.py "
    "and re-record, or it was not, in which case find out why."
)


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / SOURCE_URI
    if not source.exists():
        print(f"corpus document missing: {source}", file=sys.stderr)
        return 2

    document = parse_pdf(source, SOURCE_URI)
    fingerprint = capability_fingerprint()
    key = golden_key(fingerprint)
    record = {
        "_comment": _COMMENT,
        "key": key,
        "source_uri": SOURCE_URI,
        "recorded": datetime.now(UTC).strftime("%Y-%m-%d"),
        "fingerprint": fingerprint,
        "section_count": len(document.sections),
        "sha256": parse_digest(document),
    }
    path = golden_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(f"{'updated' if existed else 'recorded'} {path.relative_to(repo_root)}")
    print(f"  key    {key}")
    print(f"  sha256 {record['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
