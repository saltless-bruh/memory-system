"""Shared fixtures for the Scout test suite."""

from __future__ import annotations

import pytest

from scout.backends.fake import FakeRagBackend
from scout.types import RagChunk


@pytest.fixture
def chunks() -> list[RagChunk]:
    """A small mixed-source corpus: two files, varied scores."""
    return [
        RagChunk(
            text="Kerberoasting requests a TGS for an SPN and cracks it offline",
            file_path="raw/reports/acme.pdf",
            score=0.9,
            loc="p.12",
            meta={"team": "redteam"},
        ),
        RagChunk(
            text="The service account had a weak password",
            file_path="raw/reports/acme.pdf",
            score=0.5,
            loc="p.13",
        ),
        RagChunk(
            text="ESC8 relays NTLM to the AD CS web enrollment endpoint",
            file_path="raw/advisories/adcs.md",
            score=0.8,
        ),
    ]


@pytest.fixture
def backend(chunks: list[RagChunk]) -> FakeRagBackend:
    """A fake backend seeded with the sample corpus."""
    return FakeRagBackend(chunks=chunks)
