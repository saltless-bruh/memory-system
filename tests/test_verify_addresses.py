"""Scoped address verification and total CLI exit-state tests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from scout import vault
from scout.core import normalize_path, post_filter
from scout.types import Address, RagChunk, Scope, ScopedAddress
from scripts import verify_addresses as va
from scripts.verify_addresses import (
    VerifyStatus,
    _collect_addresses,
    _default_backend_factory,
    main,
    verify_address,
    verify_all,
)


@dataclass
class RecordingBackend:
    """A `RagBackend` that honours `path` pre-filtering, like the real one."""

    chunks: Sequence[RagChunk] = ()
    error: Exception | None = None
    scopes: list[Scope | None] = field(default_factory=list)
    paths: list[str | None] = field(default_factory=list)
    closed: bool = False

    async def retrieve(
        self,
        hint: str,
        *,
        path: str | None = None,
        scope: Scope | None = None,
        k: int = 10,
    ) -> Sequence[RagChunk]:
        self.scopes.append(scope)
        self.paths.append(path)
        if self.error is not None:
            raise self.error
        chunks = list(self.chunks) if path is None else post_filter(self.chunks, path)
        return chunks[:k]

    async def close(self) -> None:
        self.closed = True


def _page(
    slug: str,
    sources: list[object],
    *,
    department: str = "infra",
) -> vault.Page:
    return vault.Page(
        path=Path(f"wiki/concepts/{slug}.md"),
        frontmatter={"department": department, "sources": sources},
        body="",
    )


def _scoped(
    path: str = "raw/a.md",
    hint: str = "needle",
    department: str = "infra",
    loc: str | None = "p.1",
) -> ScopedAddress:
    return ScopedAddress(
        page_path="wiki/concepts/page.md",
        page_slug="page",
        source_index=0,
        department=department,
        address=Address(path=path, hint=hint, loc=loc),
    )


# ── B1: the discrimination harness ────────────────────────────────────────
#
# Seeded from the live measurements on `raw/reports/vllm_high_throughput_
# serving.pdf`, including the RRF magnitudes the pgvector backend produces.
# Before this gate was tightened, every one of these five hints returned PASS
# except `"the"`, because the addressed file appeared *somewhere* in a global
# top-5 window covering a fifth of the corpus.

_VLLM = "raw/reports/vllm_high_throughput_serving.pdf"
_K8S = "raw/architecture/k8s_vllm_deployment.yaml"
_ROUTER = "raw/architecture/model_routing_config.json"

_VLLM_TEXT = (
    "Page 2: Technical Specifications - PagedAttention KV-Cache Virtual Block "
    "Allocation, Continuous Batching, and Speculative Decoding."
)
_K8S_TEXT = (
    "apiVersion: apps/v1 kind: StatefulSet metadata: name: vllm-inference-cluster "
    "namespace: ai-platform replicas: 4 image: vllm/vllm-openai:v0.6.3"
)
_ROUTER_TEXT = (
    '{"version": "2026.2.0", "gateway": "LiteLLM-Enterprise-Router", '
    '"routing_rules": [{"name": "snp-embed"}]}'
)

_REAL_HINT = "PagedAttention KV-Cache Virtual Block Allocation"
_WRONG_FILE_HINT = "kubernetes deployment manifest replicas container image"
_UNRELATED_HINT = "Kerberoasting Active Directory service principal name"
_GIBBERISH_HINT = "zzqq banana marmalade unicycle wobble 8842"
_STOPWORD_HINT = "the"

#: hint -> the backend's descending-by-score result, mirroring live rankings.
_SEEDED_CORPUS: dict[str, list[tuple[str, str, float, str]]] = {
    # The minted hint: the addressed file wins the ranking outright.
    _REAL_HINT: [
        (_VLLM, _VLLM_TEXT, 0.03252, "p.2"),
        (_K8S, _K8S_TEXT, 0.01587, "Full Source Code (1/2)"),
    ],
    # Vocabulary describing a *different* file. The PDF is still retrieved —
    # which is exactly why the old top-5 membership test passed it.
    _WRONG_FILE_HINT: [
        (_K8S, _K8S_TEXT, 0.01639, "Full Source Code (1/2)"),
        (_ROUTER, _ROUTER_TEXT, 0.01613, "Full Source Code"),
        (_VLLM, _VLLM_TEXT, 0.01587, "p.2"),
    ],
    # An unrelated domain entirely; same shape, target at rank 3.
    _UNRELATED_HINT: [
        (_ROUTER, _ROUTER_TEXT, 0.01639, "Full Source Code"),
        (_K8S, _K8S_TEXT, 0.01613, "Full Source Code (1/2)"),
        (_VLLM, _VLLM_TEXT, 0.01587, "p.2"),
    ],
    # Measured live: a nonsense embedding still has a nearest neighbour, and
    # that neighbour WAS the addressed PDF at rank 1. Rank alone cannot reject
    # this one — only grounding can.
    _GIBBERISH_HINT: [
        (_VLLM, _VLLM_TEXT, 0.01639, "p.2"),
        (_K8S, _K8S_TEXT, 0.01587, "Full Source Code (1/2)"),
    ],
    # Retrieves, wins rank 1, and is no retrieval key at all.
    _STOPWORD_HINT: [
        (_VLLM, _VLLM_TEXT, 0.01639, "p.2"),
        (_ROUTER, _ROUTER_TEXT, 0.01587, "Full Source Code"),
    ],
}


@dataclass
class SeededBackend:
    """Replays a fixed ranking per hint and honours `path` pre-filtering."""

    corpus: dict[str, list[tuple[str, str, float, str]]]

    async def retrieve(
        self,
        hint: str,
        *,
        path: str | None = None,
        scope: Scope | None = None,
        k: int = 10,
    ) -> Sequence[RagChunk]:
        rows = self.corpus.get(hint, [])
        chunks = [
            RagChunk(text=text, file_path=file_path, score=score, loc=loc)
            for file_path, text, score, loc in rows
        ]
        if path is not None:
            chunks = post_filter(chunks, path)
        return chunks[:k]


@pytest.mark.parametrize(
    ("label", "hint", "expected"),
    [
        ("correct minted hint", _REAL_HINT, VerifyStatus.PASS),
        ("wrong-file vocabulary", _WRONG_FILE_HINT, VerifyStatus.DRIFT),
        ("unrelated domain", _UNRELATED_HINT, VerifyStatus.DRIFT),
        ("pure gibberish", _GIBBERISH_HINT, VerifyStatus.DRIFT),
        ("stopwords only", _STOPWORD_HINT, VerifyStatus.DRIFT),
    ],
)
async def test_verify_address_discriminates_correct_hints_from_wrong_ones(
    label: str, hint: str, expected: VerifyStatus
) -> None:
    """B1: only a hint that both wins rank 1 and is grounded may PASS."""
    report = await verify_address(
        SeededBackend(_SEEDED_CORPUS),
        _scoped(path=_VLLM, hint=hint, department="ai_eng", loc="p.2"),
    )
    assert report.status is expected, label
    if expected is not VerifyStatus.PASS:
        assert report.detail, f"{label}: a non-PASS verdict must say why"


def test_old_top_five_membership_criterion_would_have_passed_every_probe() -> None:
    """The regression these cases guard: the pre-B1 gate passed all of them.

    The old criterion was `PASS if post_filter(top-5, path)` — pure membership,
    no rank and no grounding. Replaying it over the same seeded corpus shows it
    green on wrong-file vocabulary, an unrelated domain, and gibberish alike, so
    the table above genuinely fails against the old behaviour.
    """
    for hint in (_REAL_HINT, _WRONG_FILE_HINT, _UNRELATED_HINT, _GIBBERISH_HINT):
        top_five = [
            RagChunk(text=text, file_path=file_path, score=score, loc=loc)
            for file_path, text, score, loc in _SEEDED_CORPUS[hint]
        ][:5]
        assert post_filter(top_five, _VLLM), hint  # old code: this meant PASS


async def test_gibberish_is_rejected_even_though_it_wins_rank_one() -> None:
    """Grounding, not rank, is what stops the gibberish probe."""
    backend = SeededBackend(_SEEDED_CORPUS)
    chunks = await backend.retrieve(_GIBBERISH_HINT)
    assert normalize_path(chunks[0].file_path) == _VLLM  # it IS rank 1
    assert va.holds_top_rank(chunks, _VLLM)
    assert va.grounding_coverage(_GIBBERISH_HINT, [_VLLM_TEXT]) == 0.0
    report = await verify_address(
        backend, _scoped(path=_VLLM, hint=_GIBBERISH_HINT, department="ai_eng", loc="p.2")
    )
    assert report.status is VerifyStatus.DRIFT
    assert "ungrounded" in report.detail


# ── criterion helpers ─────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PagedAttention KV-Cache", frozenset({"pagedattention", "cache"})),
        ("gemini-3.5-flash", frozenset({"gemini", "flash"})),
        ("the and with", frozenset()),
        ("", frozenset()),
    ],
)
def test_content_tokens_drops_stopwords_and_short_runs(
    text: str, expected: frozenset[str]
) -> None:
    assert va.content_tokens(text) == expected


def test_grounding_coverage_of_a_contentless_hint_is_zero() -> None:
    """A hint with no content tokens can never certify an address."""
    assert va.grounding_coverage("the of a", ["the of a"]) == 0.0


def test_grounding_coverage_counts_only_the_addressed_file_text() -> None:
    assert va.grounding_coverage("alpha beta gamma", ["alpha beta only"]) == pytest.approx(2 / 3)
    assert va.hint_is_grounded("alpha beta gamma", ["alpha beta only"])
    assert not va.hint_is_grounded("alpha beta gamma", ["alpha only"])


def test_holds_top_rank_treats_exact_score_ties_as_sharing_rank_one() -> None:
    """The backend's ORDER BY has no tiebreaker, so ties must not be flaky."""
    tied = [
        RagChunk(text="x", file_path="raw/other.md", score=0.03252),
        RagChunk(text="y", file_path="raw/a.md", score=0.03252),
        RagChunk(text="z", file_path="raw/third.md", score=0.01587),
    ]
    assert va.holds_top_rank(tied, "raw/a.md")
    assert not va.holds_top_rank(tied, "raw/third.md")
    assert not va.holds_top_rank([], "raw/a.md")


@pytest.mark.parametrize(
    ("declared", "retrieved", "expected"),
    [
        ("p.2", ("p.2", "p.1"), True),
        # the chunker's (i/n) split marker still satisfies a section locator
        ("Section System Architecture Overview",
         ("Section System Architecture Overview (1/2)",), True),
        ("Rows 1-10", ("Rows 1-4", "Rows 5-8"), False),
        ("Image Asset", ("Section Visual Overview",), False),
        ("p.2", (), False),  # nothing to verify against is not verified
        (None, ("p.2",), False),
        ("", ("p.2",), False),
    ],
)
def test_loc_is_consistent(
    declared: str | None, retrieved: tuple[str, ...], expected: bool
) -> None:
    assert va.loc_is_consistent(declared, retrieved) is expected


# ── statuses ──────────────────────────────────────────────────────────────
async def test_verify_address_passes_page_department_scope_to_backend() -> None:
    backend = RecordingBackend(
        chunks=[RagChunk(text="needle in the haystack", file_path="raw/a.md", score=1.0)]
    )
    report = await verify_address(backend, _scoped())
    assert report.status is VerifyStatus.PASS
    assert report.scoped_address.page_slug == "page"
    assert backend.scopes == [Scope(departments=frozenset({"infra"}))]
    assert backend.paths == [None]  # the ranking query is deliberately global


async def test_verify_address_fails_when_the_addressed_file_returns_nothing() -> None:
    """FAIL is reachable again: it now means *this source* has no content."""
    backend = RecordingBackend(
        chunks=[RagChunk(text="needle in the haystack", file_path="raw/other.md", score=1.0)]
    )
    report = await verify_address(backend, _scoped())
    assert report.status is VerifyStatus.FAIL
    assert report.matched_files == ("raw/other.md",)
    # it re-asked with the path pre-filter before calling the source empty
    assert backend.paths == [None, "raw/a.md"]


async def test_verify_address_drifts_when_another_file_outranks_the_target() -> None:
    backend = RecordingBackend(
        chunks=[
            RagChunk(text="needle in the haystack", file_path="raw/other.md", score=0.9),
            RagChunk(text="needle in the haystack", file_path="raw/a.md", score=0.5),
        ]
    )
    report = await verify_address(backend, _scoped())
    assert report.status is VerifyStatus.DRIFT
    assert "outranks" in report.detail


async def test_verify_address_drifts_when_target_is_below_the_diagnostic_window() -> None:
    """A file outside the display window is DRIFT, never a false FAIL."""
    filler = [
        RagChunk(text="needle in the haystack", file_path=f"raw/f{i}.md", score=1.0 - i / 100)
        for i in range(va.DIAGNOSTIC_K)
    ]
    backend = RecordingBackend(
        chunks=[*filler, RagChunk(text="needle in the haystack", file_path="raw/a.md", score=0.1)]
    )
    report = await verify_address(backend, _scoped())
    assert report.status is VerifyStatus.DRIFT
    assert backend.paths == [None, "raw/a.md"]


async def test_verify_address_reports_the_locators_the_source_carries() -> None:
    backend = RecordingBackend(
        chunks=[
            RagChunk(text="needle in the haystack", file_path="raw/a.md", score=1.0, loc="p.7"),
            RagChunk(text="needle again", file_path="raw/a.md", score=0.5, loc="p.7"),
        ]
    )
    report = await verify_address(backend, _scoped())
    assert report.status is VerifyStatus.PASS
    assert report.matched_locs == ("p.7",)


def test_collect_addresses_preserves_duplicate_page_and_source_identity() -> None:
    source = {"path": "raw/shared.md", "hint": "same", "loc": "p.1"}
    addresses = _collect_addresses(
        [_page("one", [source], department="infra"), _page("two", [source], department="redteam")]
    )
    assert len(addresses) == 2
    assert addresses[0].address == addresses[1].address
    assert {(item.page_slug, item.source_index, item.department) for item in addresses} == {
        ("one", 0, "infra"),
        ("two", 0, "redteam"),
    }


def test_collect_addresses_rejects_invalid_page_department_before_backend() -> None:
    with pytest.raises(ValueError, match="canonical"):
        _collect_addresses([_page("bad", [{"path": "raw/a", "hint": "h"}], department="all")])


async def test_verify_all_preserves_duplicate_order() -> None:
    backend = RecordingBackend(
        chunks=[RagChunk(text="one alpha beta", file_path="raw/a.md", score=1.0)]
    )
    addresses = [
        _scoped(hint="alpha"),
        ScopedAddress(
            page_path="wiki/two.md",
            page_slug="two",
            source_index=0,
            department="redteam",
            address=Address(path="raw/a.md", hint="alpha", loc="p.2"),
        ),
    ]
    reports = await verify_all(backend, addresses)
    assert [report.scoped_address for report in reports] == addresses
    assert all(report.status is VerifyStatus.PASS for report in reports)
    assert sorted(scope.departments for scope in backend.scopes if scope) == [
        frozenset({"infra"}),
        frozenset({"redteam"}),
    ]


# ── total CLI exit contract: 0 pass · 1 semantic · 2 infrastructure ────────
def test_main_returns_0_for_pass_and_closes_backend() -> None:
    backend = RecordingBackend(
        [RagChunk(text="kerberoasting service tickets", file_path="raw/a.md", score=1.0)]
    )
    rc = main(
        backend_factory=lambda: backend,
        pages_loader=lambda: [
            _page("page", [{"path": "raw/a.md", "hint": "kerberoasting", "loc": "p.1"}])
        ],
    )
    assert rc == 0
    assert backend.closed


def test_main_returns_1_when_the_hint_is_ungrounded(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A tightened criterion still exits 1, never 2 — it is a semantic verdict."""
    backend = RecordingBackend(
        [RagChunk(text="entirely unrelated prose", file_path="raw/a.md", score=1.0)]
    )
    rc = main(
        backend_factory=lambda: backend,
        pages_loader=lambda: [
            _page("page", [{"path": "raw/a.md", "hint": "kerberoasting", "loc": "p.1"}])
        ],
    )
    assert rc == 1
    assert "ungrounded" in capsys.readouterr().out
    assert backend.closed


def test_main_returns_1_only_for_semantic_failure_and_closes_backend() -> None:
    backend = RecordingBackend()
    rc = main(
        backend_factory=lambda: backend,
        pages_loader=lambda: [_page("page", [{"path": "raw/a.md", "hint": "none", "loc": "p.1"}])],
    )
    assert rc == 1
    assert backend.closed


def test_main_reports_a_stale_locator_without_changing_the_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """m2: a decorative `loc` is surfaced as an advisory, not a merge blocker."""
    backend = RecordingBackend(
        [
            RagChunk(
                text="kerberoasting service tickets",
                file_path="raw/a.md",
                score=1.0,
                loc="Rows 1-4",
            )
        ]
    )
    rc = main(
        backend_factory=lambda: backend,
        pages_loader=lambda: [
            _page("page", [{"path": "raw/a.md", "hint": "kerberoasting", "loc": "Rows 1-10"}])
        ],
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "PASS " in out
    assert "'Rows 1-10' is not among the locators retrieved" in out


def test_main_returns_2_for_infrastructure_error_redacts_and_closes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "database-password-must-not-appear"
    backend = RecordingBackend(error=ConnectionError(secret))
    rc = main(
        backend_factory=lambda: backend,
        pages_loader=lambda: [_page("page", [{"path": "raw/a.md", "hint": "x", "loc": "p.1"}])],
    )
    assert rc == 2
    assert backend.closed
    assert secret not in capsys.readouterr().out


def test_main_returns_2_for_vault_or_factory_error() -> None:
    def factory_boom() -> RecordingBackend:
        raise RuntimeError("secret")

    assert main(backend_factory=factory_boom) == 2
    backend = RecordingBackend()

    def vault_boom() -> list[vault.Page]:
        raise OSError("secret")

    assert main(backend_factory=lambda: backend, pages_loader=vault_boom) == 2
    assert backend.closed


def test_production_backend_factory_ignores_fake_embedder_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USE_FAKE_EMBEDDER", "true")
    from scout.backends.pgvector import PgVectorRlsBackend

    assert isinstance(_default_backend_factory(), PgVectorRlsBackend)
