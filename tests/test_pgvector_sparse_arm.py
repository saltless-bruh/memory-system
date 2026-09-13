"""Pin the lexical arm's two-tier query construction.

`plainto_tsquery` AND-joins every lexeme, so before 2026-09-13 the sparse-only
degradation path required all of a question's stems inside a single chunk and 35
of the 40 benchmark queries matched nothing at all. Relaxing that conjunction
everywhere was measured and rejected -- it cost hybrid recall@1 en 0.95 -> 0.85
and vi 0.80 -> 0.60 -- so the two paths now use deliberately different tiers.
These tests fail against either the original form or a uniform relaxation.
"""

from __future__ import annotations

import re

from scout.backends import pgvector

_SITE = re.compile(r"(?:replace\()?plainto_tsquery\('(\w+)', (\$\d+)\)")


def _sites(sql: str) -> list[tuple[str, str, bool]]:
    """Return (config, placeholder, relaxed) for every tsquery site in `sql`."""
    found = []
    for match in _SITE.finditer(sql):
        relaxed = match.group(0).startswith("replace(")
        found.append((match.group(1), match.group(2), relaxed))
    return found


def test_sparse_only_path_is_fully_relaxed() -> None:
    """Without a dense arm a conjunction returns nothing, so every site relaxes."""
    sites = _sites(pgvector._SPARSE_QUERY)
    assert sites, "the sparse query lost its lexical arm entirely"
    assert all(relaxed for _config, _param, relaxed in sites), (
        "the degradation path must not use a conjunction; it is the only arm there"
    )


def test_hybrid_path_stays_strict() -> None:
    """With dense supplying recall, the lexical arm is for exact-term precision."""
    sites = _sites(pgvector._HYBRID_QUERY)
    assert sites, "the hybrid query lost its lexical arm entirely"
    assert not any(relaxed for _config, _param, relaxed in sites), (
        "relaxing the hybrid arm was measured to cost recall@1; keep it strict"
    )


def test_relaxation_rewrites_conjunction_as_disjunction_in_that_direction() -> None:
    expression = pgvector._relaxed_tsquery("english", "$4")
    assert "plainto_tsquery('english', $4)" in expression
    assert "'&', '|'" in expression
    assert "'|', '&'" not in expression
    assert expression.endswith("::tsquery")


def test_strict_builder_adds_no_rewrite() -> None:
    assert pgvector._strict_tsquery("simple", "$3") == "plainto_tsquery('simple', $3)"


def test_both_language_arms_participate_in_every_tier() -> None:
    for build in (pgvector._strict_tsquery, pgvector._relaxed_tsquery):
        match = pgvector._match(build, "$3")
        rank = pgvector._rank(build, "$3")
        for expression in (match, rank):
            assert "c.tsv_simple" in expression
            assert "'english'" in expression
            assert "'simple'" in expression
        # A chunk need satisfy only one arm to be a candidate.
        assert " OR " in match
        # Ranking sums the arms rather than discarding one.
        assert "+ COALESCE" in rank


def test_every_site_binds_the_caller_placeholder() -> None:
    # A hard-coded placeholder would silently search the wrong bind parameter.
    for sql, expected in (
        (pgvector._HYBRID_QUERY, "$4"),
        (pgvector._SPARSE_QUERY, "$3"),
    ):
        params = {param for _config, param, _relaxed in _sites(sql)}
        assert params == {expected}, f"expected only {expected}, found {sorted(params)}"


def test_both_tiers_cover_both_text_configurations() -> None:
    for sql in (pgvector._HYBRID_QUERY, pgvector._SPARSE_QUERY):
        configs = {config for config, _param, _relaxed in _sites(sql)}
        assert configs == {"english", "simple"}


def test_fusion_policy_constants_are_unchanged() -> None:
    # The repair is confined to query construction; ranking policy is untouched,
    # and no per-arm penalty survives -- the tier split replaced that attempt.
    assert pgvector.DEFAULT_RRF_K == 60
    assert pgvector.DEFAULT_RAW_RANK_PENALTY == 15
    assert pgvector.DEFAULT_CONTESTED_RANK_PENALTY == 30
    assert not hasattr(pgvector, "DEFAULT_SPARSE_RANK_PENALTY")
    assert "$10" not in pgvector._HYBRID_QUERY
