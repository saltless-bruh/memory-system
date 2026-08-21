"""Tests for the fail-closed, atomic wiki compiler."""

from __future__ import annotations

import io
import json
import re
import subprocess
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scout.parsers import ParsedDocument, ParsedSection, ParserError, parse_file
from scout.types import Address
from scripts.compile_note import (
    MAX_EXTRACTED_CHARS,
    CompileNoteError,
    GeneratedBody,
    GeneratedMetadata,
    _validate_generated_body,
    compile_note,
    generate_model_data,
    generate_page_body,
    main,
)
from scripts.mint import MintResult, MintStatus
from scripts.verify_groundedness import (
    GroundednessReport,
    PageVerdict,
    SourceContext,
)


@pytest.fixture
def compiler_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    raw = tmp_path / "raw" / "reports"
    raw.mkdir(parents=True)
    source = raw / "acme.md"
    source.write_text("# Acme\n\nSource facts.\n", encoding="utf-8")
    wiki = tmp_path / "wiki"
    for directory in ("concepts", "techniques", "entities", "playbooks"):
        (wiki / directory).mkdir(parents=True)
    (wiki / "index.md").write_bytes(b"original index\n")
    monkeypatch.setattr("scripts.compile_note.REPO_ROOT", tmp_path)
    monkeypatch.setattr("scripts.compile_note._current_branch", lambda: "feature/wiki")
    return tmp_path, "raw/reports/acme.md"


@pytest.fixture
def valid_metadata() -> GeneratedMetadata:
    return GeneratedMetadata(
        entities=("acme", "capability"),
        hint="Acme source facts",
    )


@pytest.fixture
def minted() -> object:
    async def _mint(
        backend: object,
        path: str,
        candidate_hints: object,
        *,
        department: str,
        loc: str | None = None,
    ) -> MintResult:
        return MintResult(
            path=path,
            department=department,
            address=Address(path=path, hint="Acme source facts", loc=loc),
            status=MintStatus.MINTED,
            tried=(),
        )

    return _mint


def _success_index_run(repo: Path) -> subprocess.CompletedProcess[str]:
    (repo / "wiki" / "index.md").write_bytes(b"regenerated index\n")
    return subprocess.CompletedProcess([], 0, "", "")


def _wire_body_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    body: GeneratedBody | None = None,
    verdict: GroundednessReport | None = None,
) -> None:
    """Wire the post-mint grounding seams for tests not exercising them."""
    passages = [
        SourceContext(path="raw/reports/acme.md", loc="line 1", text="Source facts.")
    ]

    async def _collect(
        *_a: object, **_k: object
    ) -> tuple[list[SourceContext], list[str]]:
        return passages, []

    monkeypatch.setattr("scripts.compile_note.collect_context", _collect)
    monkeypatch.setattr(
        "scripts.compile_note.generate_page_body",
        lambda *_a, **_k: body
        or GeneratedBody("A grounded sentence.", ("A grounded technical claim.",)),
    )

    async def _verify(*_a: object, **_k: object) -> GroundednessReport:
        return verdict or GroundednessReport("p", PageVerdict.GROUNDED)

    monkeypatch.setattr("scripts.compile_note.verify_page", _verify)
    monkeypatch.setattr(
        "scripts.compile_note.LiteLLMJudge", MagicMock(from_env=lambda: MagicMock())
    )


def test_path_escape_fails_before_parser_or_model(
    compiler_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _path = compiler_repo
    outside = repo / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    parser = MagicMock()
    model = MagicMock()
    monkeypatch.setattr("scripts.compile_note.parse_file", parser)
    monkeypatch.setattr("scripts.compile_note.generate_model_data", model)

    with pytest.raises(CompileNoteError, match="beneath raw"):
        compile_note(
            "raw/../outside.md", "Escape", "concept", department="infra", loc="line 1"
        )

    parser.assert_not_called()
    model.assert_not_called()


def test_source_symlink_escape_fails_before_parser(
    compiler_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _path = compiler_repo
    outside = repo / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (repo / "raw" / "escape.md").symlink_to(outside)
    parser = MagicMock()
    monkeypatch.setattr("scripts.compile_note.parse_file", parser)

    with pytest.raises(CompileNoteError, match="beneath raw"):
        compile_note(
            "raw/escape.md", "Escape", "concept", department="infra", loc="line 1"
        )

    parser.assert_not_called()


def test_symlinked_destination_category_fails_before_parser_or_model(
    compiler_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, path = compiler_repo
    category = repo / "wiki" / "concepts"
    category.rmdir()
    outside = repo / "outside-wiki"
    outside.mkdir()
    category.symlink_to(outside, target_is_directory=True)
    parser = MagicMock()
    model = MagicMock()
    monkeypatch.setattr("scripts.compile_note.parse_file", parser)
    monkeypatch.setattr("scripts.compile_note.generate_model_data", model)

    with pytest.raises(CompileNoteError, match="category"):
        compile_note(
            path,
            "Escaped Destination",
            "concept",
            department="infra",
            loc="line 1",
        )

    assert not (outside / "escaped-destination.md").exists()
    parser.assert_not_called()
    model.assert_not_called()


def test_symlinked_wiki_page_fails_closed_as_compiler_error(
    compiler_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, path = compiler_repo
    outside = repo / "outside-page.md"
    outside.write_text("outside", encoding="utf-8")
    (repo / "wiki" / "concepts" / "escape.md").symlink_to(outside)
    parser = MagicMock()
    model = MagicMock()
    monkeypatch.setattr("scripts.compile_note.parse_file", parser)
    monkeypatch.setattr("scripts.compile_note.generate_model_data", model)

    with pytest.raises(CompileNoteError, match="wiki tree"):
        compile_note(
            path,
            "Unsafe Vault",
            "concept",
            department="infra",
            loc="line 1",
        )

    parser.assert_not_called()
    model.assert_not_called()


def test_parser_failure_aborts_before_model_and_writes(
    compiler_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, path = compiler_repo
    monkeypatch.setattr(
        "scripts.compile_note.parse_file",
        MagicMock(side_effect=ParserError("bad source")),
    )
    model = MagicMock()
    monkeypatch.setattr("scripts.compile_note.generate_model_data", model)

    with pytest.raises(CompileNoteError, match="parse"):
        compile_note(path, "Bad Source", "concept", department="infra", loc="line 1")

    model.assert_not_called()
    assert not (repo / "wiki" / "concepts" / "bad-source.md").exists()


def test_empty_parser_output_aborts_before_model(
    compiler_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo, path = compiler_repo
    document = ParsedDocument(path, "Empty", [ParsedSection("line 1", "")])
    monkeypatch.setattr(
        "scripts.compile_note.parse_file", lambda *_args, **_kwargs: document
    )
    model = MagicMock()
    monkeypatch.setattr("scripts.compile_note.generate_model_data", model)

    with pytest.raises(CompileNoteError, match="no extractable text"):
        compile_note(path, "Empty", "concept", department="infra", loc="line 1")

    model.assert_not_called()


def test_model_request_uses_configured_gateway_bounded_data_and_injection_delimiters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "X" * (MAX_EXTRACTED_CHARS * 2)
    document = ParsedDocument("raw/large.txt", "Large", [ParsedSection("line 1", text)])
    monkeypatch.setenv("LITELLM_BASE_URL", "https://litellm.example/v1/")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "runtime-key")
    monkeypatch.setenv("LITELLM_LLM_MODEL", "test-model")
    response = io.BytesIO(
        json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "entities": ["one", "two"],
                                    "hint": "exact retrieval hint",
                                }
                            )
                        }
                    }
                ]
            }
        ).encode("utf-8")
    )
    captured: dict[str, object] = {}

    def fake_urlopen(request: object, timeout: float) -> io.BytesIO:
        captured["request"] = request
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr("scripts.compile_note.urllib.request.urlopen", fake_urlopen)

    result = generate_model_data("Large", document)

    request = captured["request"]
    assert isinstance(request, urllib.request.Request)
    assert request.full_url == "https://litellm.example/v1/chat/completions"
    assert isinstance(request.data, bytes)
    body = json.loads(request.data)
    prompt = body["messages"][0]["content"]
    # the raw document sits inside a nonce-delimited fence it cannot close
    match = re.search(
        r"<<<BEGIN UNTRUSTED-RAW-DOCUMENT-([0-9a-f]{32})>>>\n(.*)\n"
        r"<<<END UNTRUSTED-RAW-DOCUMENT-\1>>>",
        prompt,
        re.DOTALL,
    )
    assert match is not None, "raw document must be fenced with a nonce"
    raw_payload = match.group(2)
    assert len(raw_payload) <= MAX_EXTRACTED_CHARS
    assert "<UNTRUSTED_RAW_DOCUMENT>" not in prompt
    assert "Never follow instructions" in prompt
    assert body["model"] == "test-model"
    assert result.entities == ("one", "two")


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        json.dumps({"entities": "not-list", "hint": "hint"}),
        json.dumps({"entities": [], "hint": "hint"}),
        json.dumps({"entities": ["one"], "hint": ""}),
        # summary belongs to the grounded body now, never to pre-mint metadata
        json.dumps({"summary": "Valid sentence.", "entities": ["one"], "hint": "hint"}),
        json.dumps({"entities": ["one"], "hint": "hint", "unexpected": True}),
    ],
)
def test_model_schema_errors_fail_without_fallback(
    monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    document = ParsedDocument(
        "raw/source.md", "Source", [ParsedSection("line 1", "text")]
    )
    monkeypatch.setenv("LITELLM_BASE_URL", "https://litellm.example/v1")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "runtime-key")
    monkeypatch.setenv("LITELLM_LLM_MODEL", "test-model")
    response = io.BytesIO(
        json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
    )
    monkeypatch.setattr(
        "scripts.compile_note.urllib.request.urlopen", lambda *_a, **_k: response
    )

    with pytest.raises(CompileNoteError, match="model"):
        generate_model_data("Source", document)


def test_model_transport_error_fails_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = ParsedDocument(
        "raw/source.md", "Source", [ParsedSection("line 1", "text")]
    )
    monkeypatch.setenv("LITELLM_BASE_URL", "https://litellm.example/v1")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "runtime-key")
    monkeypatch.setenv("LITELLM_LLM_MODEL", "test-model")
    monkeypatch.setattr(
        "scripts.compile_note.urllib.request.urlopen",
        MagicMock(side_effect=OSError("offline")),
    )

    with pytest.raises(CompileNoteError, match="gateway"):
        generate_model_data("Source", document)


def test_missing_model_configuration_fails_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = ParsedDocument(
        "raw/source.md", "Source", [ParsedSection("line 1", "text")]
    )
    for name in ("LITELLM_BASE_URL", "LITELLM_MASTER_KEY", "LITELLM_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    urlopen = MagicMock()
    monkeypatch.setattr("scripts.compile_note.urllib.request.urlopen", urlopen)

    with pytest.raises(CompileNoteError, match="configuration"):
        generate_model_data("Source", document)

    urlopen.assert_not_called()


def test_compile_success_mints_with_department_scope_and_explicit_loc(
    compiler_repo: tuple[Path, str],
    valid_metadata: GeneratedMetadata,
    minted: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, path = compiler_repo
    document = parse_file(repo / path, repo)
    mint_mock = MagicMock(side_effect=minted)
    backend = MagicMock()
    monkeypatch.setattr("scripts.compile_note.parse_file", lambda *_a, **_k: document)
    monkeypatch.setattr(
        "scripts.compile_note.generate_model_data", lambda *_a: valid_metadata
    )
    monkeypatch.setattr("scripts.compile_note.mint_address", mint_mock)
    monkeypatch.setattr(
        "scripts.compile_note.PgVectorRlsBackend", MagicMock(return_value=backend)
    )
    monkeypatch.setattr(
        "scripts.compile_note.subprocess.run",
        lambda *_a, **_k: _success_index_run(repo),
    )
    _wire_body_seams(monkeypatch)

    page = compile_note(
        path,
        "Acme Capability",
        "concept",
        department="blueteam",
        loc="Section Acme",
    )

    assert page == repo / "wiki" / "concepts" / "acme-capability.md"
    content = page.read_text(encoding="utf-8")
    assert "department: blueteam" in content
    assert "loc: Section Acme" in content
    assert "[[index]]" not in content
    call = mint_mock.call_args
    assert call.kwargs["loc"] == "Section Acme"
    assert call.kwargs["department"] == "blueteam"
    backend.close.assert_called_once_with()
    assert (repo / "wiki" / "index.md").read_bytes() == b"regenerated index\n"


def test_valid_optional_wikilink_is_rendered(
    compiler_repo: tuple[Path, str],
    valid_metadata: GeneratedMetadata,
    minted: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, path = compiler_repo
    related = repo / "wiki" / "concepts" / "related-page.md"
    related.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(
        "scripts.compile_note.generate_model_data", lambda *_a: valid_metadata
    )
    monkeypatch.setattr(
        "scripts.compile_note.mint_address", MagicMock(side_effect=minted)
    )
    monkeypatch.setattr("scripts.compile_note.PgVectorRlsBackend", MagicMock())
    monkeypatch.setattr(
        "scripts.compile_note.subprocess.run",
        lambda *_a, **_k: _success_index_run(repo),
    )
    _wire_body_seams(monkeypatch)

    page = compile_note(
        path,
        "Linked Page",
        "concept",
        department="infra",
        loc="line 1",
        wikilinks=("related-page",),
    )

    assert "[[related-page]]" in page.read_text(encoding="utf-8")


def test_invalid_or_missing_wikilink_fails_before_model(
    compiler_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo, path = compiler_repo
    model = MagicMock()
    monkeypatch.setattr("scripts.compile_note.generate_model_data", model)

    with pytest.raises(CompileNoteError, match="wikilink"):
        compile_note(
            path,
            "Bad Link",
            "concept",
            department="infra",
            loc="line 1",
            wikilinks=("../escape",),
        )

    model.assert_not_called()


def test_protected_branch_and_existing_page_are_rejected_before_model(
    compiler_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, path = compiler_repo
    model = MagicMock()
    monkeypatch.setattr("scripts.compile_note.generate_model_data", model)
    monkeypatch.setattr("scripts.compile_note._current_branch", lambda: "main")
    with pytest.raises(CompileNoteError, match="protected branch"):
        compile_note(path, "Protected", "concept", department="infra", loc="line 1")
    monkeypatch.setattr("scripts.compile_note._current_branch", lambda: "feature/wiki")
    existing = repo / "wiki" / "concepts" / "existing.md"
    existing.write_bytes(b"original page\n")
    with pytest.raises(CompileNoteError, match="already exists"):
        compile_note(path, "Existing", "concept", department="infra", loc="line 1")
    assert existing.read_bytes() == b"original page\n"
    model.assert_not_called()


def test_mint_failure_leaves_page_and_index_unchanged(
    compiler_repo: tuple[Path, str],
    valid_metadata: GeneratedMetadata,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, path = compiler_repo
    index_before = (repo / "wiki" / "index.md").read_bytes()

    async def failed_mint(*_args: object, **_kwargs: object) -> MintResult:
        return MintResult(
            path=path,
            department="infra",
            address=None,
            status=MintStatus.NO_HINT_WORKS,
            tried=(),
        )

    monkeypatch.setattr(
        "scripts.compile_note.generate_model_data", lambda *_a: valid_metadata
    )
    monkeypatch.setattr("scripts.compile_note.mint_address", failed_mint)
    monkeypatch.setattr("scripts.compile_note.PgVectorRlsBackend", MagicMock())

    with pytest.raises(CompileNoteError, match="mint"):
        compile_note(path, "Mint Failure", "concept", department="infra", loc="line 1")

    assert not (repo / "wiki" / "concepts" / "mint-failure.md").exists()
    assert (repo / "wiki" / "index.md").read_bytes() == index_before


def test_candidate_lint_failure_leaves_page_and_index_unchanged(
    compiler_repo: tuple[Path, str],
    minted: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, path = compiler_repo
    invalid_body = GeneratedBody("First sentence. Second sentence.", ("A claim.",))
    index_before = (repo / "wiki" / "index.md").read_bytes()
    monkeypatch.setattr(
        "scripts.compile_note.generate_model_data",
        lambda *_a: GeneratedMetadata(("entity",), "hint"),
    )
    mint_mock = MagicMock(side_effect=minted)
    monkeypatch.setattr("scripts.compile_note.mint_address", mint_mock)
    monkeypatch.setattr("scripts.compile_note.PgVectorRlsBackend", MagicMock())
    _wire_body_seams(monkeypatch, body=invalid_body)

    with pytest.raises(CompileNoteError, match="candidate"):
        compile_note(path, "Lint Failure", "concept", department="infra", loc="line 1")

    assert not (repo / "wiki" / "concepts" / "lint-failure.md").exists()
    assert (repo / "wiki" / "index.md").read_bytes() == index_before


def test_index_failure_atomically_rolls_back_page_and_index(
    compiler_repo: tuple[Path, str],
    valid_metadata: GeneratedMetadata,
    minted: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, path = compiler_repo
    index = repo / "wiki" / "index.md"
    index_before = index.read_bytes()
    monkeypatch.setattr(
        "scripts.compile_note.generate_model_data", lambda *_a: valid_metadata
    )
    monkeypatch.setattr(
        "scripts.compile_note.mint_address", MagicMock(side_effect=minted)
    )
    monkeypatch.setattr("scripts.compile_note.PgVectorRlsBackend", MagicMock())
    _wire_body_seams(monkeypatch)

    def failed_index(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        index.write_bytes(b"partially changed\n")
        return subprocess.CompletedProcess([], 1, "", "lint failed")

    monkeypatch.setattr("scripts.compile_note.subprocess.run", failed_index)

    with pytest.raises(CompileNoteError, match="index"):
        compile_note(path, "Rollback", "concept", department="infra", loc="line 1")

    assert not (repo / "wiki" / "concepts" / "rollback.md").exists()
    assert index.read_bytes() == index_before
    assert not list((repo / "wiki").rglob("*.tmp"))


def test_cli_requires_department_and_location(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "compile_note.py",
            "--path",
            "raw/source.md",
            "--title",
            "Missing Scope",
            "--category",
            "concept",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2


def test_parse_file_rejects_unsupported_binary(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"\x00\x01")

    with pytest.raises(ParserError, match="unsupported"):
        parse_file(source, tmp_path)


def test_parse_file_propagates_pdf_extraction_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"not a pdf")
    monkeypatch.setattr("pypdf.PdfReader", MagicMock(side_effect=ValueError("bad pdf")))

    with pytest.raises(ParserError, match="Could not parse PDF"):
        parse_file(source, tmp_path)


# ── grounded body generation ─────────────────────────────────────────────────


@pytest.fixture
def valid_body() -> GeneratedBody:
    return GeneratedBody(
        summary="Acme provides one grounded technical capability.",
        specifications=(
            "Acme sustains 4.2 requests per second on the measured configuration.",
            "The scheduler admits a request only when its KV blocks are resident.",
        )
    )


@pytest.fixture
def retrieved() -> tuple[SourceContext, ...]:
    return (
        SourceContext(
            path="raw/reports/acme.md",
            loc="Section Acme",
            text="Acme sustains 4.2 requests per second on the measured configuration.",
        ),
        SourceContext(
            path="raw/reports/acme.md",
            loc="Section Acme",
            text="The scheduler admits a request only when its KV blocks are resident.",
        ),
    )


def _grounded_report() -> GroundednessReport:
    return GroundednessReport("wiki/concepts/acme-capability.md", PageVerdict.GROUNDED)


def _unsupported_report() -> GroundednessReport:
    from scripts.verify_groundedness import UnsupportedClaim

    return GroundednessReport(
        "wiki/concepts/acme-capability.md",
        PageVerdict.UNSUPPORTED,
        claims=(
            UnsupportedClaim(
                sentence="Acme sustains 9.9 requests per second.",
                reason="the passages state 4.2, not 9.9",
                anchored=True,
            ),
        ),
    )


def _wire_grounded_compile(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    *,
    document: object,
    metadata: GeneratedMetadata,
    minted: object,
    retrieved: tuple[SourceContext, ...],
    body_generator: object,
    reports: list[GroundednessReport],
) -> MagicMock:
    """Wire every seam of the compile pipeline, returning the backend mock."""
    backend = MagicMock()
    monkeypatch.setattr("scripts.compile_note.parse_file", lambda *_a, **_k: document)
    monkeypatch.setattr(
        "scripts.compile_note.generate_model_data", lambda *_a: metadata
    )
    monkeypatch.setattr(
        "scripts.compile_note.mint_address", MagicMock(side_effect=minted)
    )
    monkeypatch.setattr(
        "scripts.compile_note.PgVectorRlsBackend", MagicMock(return_value=backend)
    )

    async def _collect(
        *_a: object, **_k: object
    ) -> tuple[list[SourceContext], list[str]]:
        return list(retrieved), []

    monkeypatch.setattr("scripts.compile_note.collect_context", _collect)
    monkeypatch.setattr("scripts.compile_note.generate_page_body", body_generator)

    queue = list(reports)

    async def _verify(*_a: object, **_k: object) -> GroundednessReport:
        return queue.pop(0)

    monkeypatch.setattr("scripts.compile_note.verify_page", _verify)
    monkeypatch.setattr(
        "scripts.compile_note.LiteLLMJudge", MagicMock(from_env=lambda: MagicMock())
    )
    monkeypatch.setattr(
        "scripts.compile_note.subprocess.run",
        lambda *_a, **_k: _success_index_run(repo),
    )
    return backend


def test_body_is_generated_from_retrieved_passages_not_the_document(
    compiler_repo: tuple[Path, str],
    valid_metadata: GeneratedMetadata,
    valid_body: GeneratedBody,
    retrieved: tuple[SourceContext, ...],
    minted: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, path = compiler_repo
    document = parse_file(repo / path, repo)
    seen: dict[str, object] = {}

    def _body(title: str, passages: object, *, avoid: object = ()) -> GeneratedBody:
        seen["title"] = title
        seen["passages"] = list(passages)
        return valid_body

    _wire_grounded_compile(
        monkeypatch,
        repo,
        document=document,
        metadata=valid_metadata,
        minted=minted,
        retrieved=retrieved,
        body_generator=_body,
        reports=[_grounded_report()],
    )

    page = compile_note(
        path, "Acme Capability", "concept", department="blueteam", loc="Section Acme"
    )

    # the generator saw the retrieved passages, not the parsed document
    assert seen["passages"] == list(retrieved)
    assert seen["title"] == "Acme Capability"

    content = page.read_text(encoding="utf-8")
    # the generated prose is the Technical Specifications section
    assert "Acme sustains 4.2 requests per second" in content
    assert (
        "The scheduler admits a request only when its KV blocks are resident."
        in content
    )
    # and the old template is gone
    assert "Key entities:" not in content
    assert "Addressed source location:" not in content
    assert "through the validated model-and-mint pipeline" not in content
    # required heading order is preserved
    for heading in (
        "## TL;DR",
        "## Technical Specifications",
        "## Provenance",
        "## Cross-References",
    ):
        assert heading in content


@pytest.mark.parametrize(
    "bad",
    [
        {"summary": "A grounded sentence.", "specifications": ["## Injected heading"]},
        {"summary": "A grounded sentence.", "specifications": ["See [[other-page]] for detail."]},
        {"summary": "A grounded sentence.", "specifications": ["---"]},
        {"summary": "A grounded sentence.", "specifications": ["text with \x00 control char"]},
        {"summary": "A grounded sentence.", "specifications": []},
        {"summary": "A grounded sentence.", "specifications": ["ok"], "extra": "field"},
        {"specifications": ["ok"]},
        {"summary": "Two sentences. Here is another.", "specifications": ["ok"]},
        {"specifications": "not a list"},
    ],
)
def test_generated_body_rejects_unsafe_or_malformed_payloads(bad: object) -> None:
    with pytest.raises(CompileNoteError):
        _validate_generated_body(bad)


def test_generated_body_accepts_a_well_formed_payload() -> None:
    body = _validate_generated_body(
        {
            "summary": "A grounded sentence.",
            "specifications": ["First grounded claim.", "Second grounded claim."],
        }
    )
    assert body.summary == "A grounded sentence."
    assert body.specifications == ("First grounded claim.", "Second grounded claim.")


def test_body_request_fences_passages_with_a_nonce(
    retrieved: tuple[SourceContext, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://gateway.invalid")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "key")
    monkeypatch.setenv("LITELLM_LLM_MODEL", "snp-llm")
    captured: dict[str, object] = {}

    def _urlopen(request: object, timeout: float = 0) -> object:
        captured["body"] = json.loads(request.data)  # type: ignore[attr-defined]
        payload = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"summary": "A grounded sentence.", "specifications": ["A grounded claim."]}
                            )
                        }
                    }
                ]
            }
        )
        return io.BytesIO(payload.encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", MagicMock(side_effect=_urlopen))
    result = generate_page_body("Acme Capability", retrieved)

    assert result.specifications == ("A grounded claim.",)
    prompt = captured["body"]["messages"][0]["content"]  # type: ignore[index]
    assert "4.2 requests per second" in prompt
    # nonce-delimited fence, not a static tag
    assert "<<<BEGIN UNTRUSTED-SOURCE-PASSAGES-" in prompt
    assert "<UNTRUSTED_SOURCE_PASSAGES>" not in prompt


def test_unsupported_candidate_is_not_written_and_index_is_unchanged(
    compiler_repo: tuple[Path, str],
    valid_metadata: GeneratedMetadata,
    valid_body: GeneratedBody,
    retrieved: tuple[SourceContext, ...],
    minted: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, path = compiler_repo
    document = parse_file(repo / path, repo)
    _wire_grounded_compile(
        monkeypatch,
        repo,
        document=document,
        metadata=valid_metadata,
        minted=minted,
        retrieved=retrieved,
        body_generator=lambda *_a, **_k: valid_body,
        # both the first attempt and the single retry come back unsupported
        reports=[_unsupported_report(), _unsupported_report()],
    )

    with pytest.raises(CompileNoteError, match="9.9"):
        compile_note(
            path,
            "Acme Capability",
            "concept",
            department="blueteam",
            loc="Section Acme",
        )

    assert not (repo / "wiki" / "concepts" / "acme-capability.md").exists()
    assert (repo / "wiki" / "index.md").read_bytes() == b"original index\n"


def test_unsupported_first_attempt_is_retried_once_then_written(
    compiler_repo: tuple[Path, str],
    valid_metadata: GeneratedMetadata,
    valid_body: GeneratedBody,
    retrieved: tuple[SourceContext, ...],
    minted: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, path = compiler_repo
    document = parse_file(repo / path, repo)
    calls: list[object] = []

    def _body(title: str, passages: object, *, avoid: object = ()) -> GeneratedBody:
        calls.append(list(avoid))
        return valid_body

    _wire_grounded_compile(
        monkeypatch,
        repo,
        document=document,
        metadata=valid_metadata,
        minted=minted,
        retrieved=retrieved,
        body_generator=_body,
        reports=[_unsupported_report(), _grounded_report()],
    )

    page = compile_note(
        path, "Acme Capability", "concept", department="blueteam", loc="Section Acme"
    )

    assert page.exists()
    assert len(calls) == 2, "the body should be regenerated exactly once on retry"
    assert calls[0] == []
    assert "Acme sustains 9.9 requests per second." in calls[1], (
        "the retry must be told which sentence was unsupported"
    )


def test_empty_retrieved_context_refuses_before_generating_a_body(
    compiler_repo: tuple[Path, str],
    valid_metadata: GeneratedMetadata,
    minted: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, path = compiler_repo
    document = parse_file(repo / path, repo)
    generator = MagicMock()
    backend = MagicMock()
    monkeypatch.setattr("scripts.compile_note.parse_file", lambda *_a, **_k: document)
    monkeypatch.setattr(
        "scripts.compile_note.generate_model_data", lambda *_a: valid_metadata
    )
    monkeypatch.setattr(
        "scripts.compile_note.mint_address", MagicMock(side_effect=minted)
    )
    monkeypatch.setattr(
        "scripts.compile_note.PgVectorRlsBackend", MagicMock(return_value=backend)
    )

    async def _collect(
        *_a: object, **_k: object
    ) -> tuple[list[SourceContext], list[str]]:
        return [], ["raw/reports/acme.md"]

    monkeypatch.setattr("scripts.compile_note.collect_context", _collect)
    monkeypatch.setattr("scripts.compile_note.generate_page_body", generator)

    with pytest.raises(CompileNoteError, match="raw/reports/acme.md"):
        compile_note(
            path,
            "Acme Capability",
            "concept",
            department="blueteam",
            loc="Section Acme",
        )

    generator.assert_not_called()
    assert not (repo / "wiki" / "concepts" / "acme-capability.md").exists()
    assert (repo / "wiki" / "index.md").read_bytes() == b"original index\n"


def test_unconfigured_judge_reports_cleanly_and_writes_nothing(
    compiler_repo: tuple[Path, str],
    valid_metadata: GeneratedMetadata,
    retrieved: tuple[SourceContext, ...],
    minted: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing judge must be a clean error, not a traceback out of main()."""
    from scripts.verify_groundedness import GroundednessError

    repo, path = compiler_repo
    document = parse_file(repo / path, repo)
    monkeypatch.setattr("scripts.compile_note.parse_file", lambda *_a, **_k: document)
    monkeypatch.setattr(
        "scripts.compile_note.generate_model_data", lambda *_a: valid_metadata
    )
    monkeypatch.setattr(
        "scripts.compile_note.mint_address", MagicMock(side_effect=minted)
    )
    monkeypatch.setattr("scripts.compile_note.PgVectorRlsBackend", MagicMock())

    async def _collect(*_a: object, **_k: object) -> tuple[list[SourceContext], list[str]]:
        return list(retrieved), []

    monkeypatch.setattr("scripts.compile_note.collect_context", _collect)

    def _raise() -> object:
        raise GroundednessError("missing judge configuration: LITELLM_BASE_URL")

    monkeypatch.setattr(
        "scripts.compile_note.LiteLLMJudge", MagicMock(from_env=_raise)
    )

    with pytest.raises(CompileNoteError, match="not configured"):
        compile_note(
            path, "Acme Capability", "concept", department="blueteam", loc="Section Acme"
        )

    assert not (repo / "wiki" / "concepts" / "acme-capability.md").exists()
    assert (repo / "wiki" / "index.md").read_bytes() == b"original index\n"


def test_generation_requests_pin_temperature_to_zero(
    retrieved: tuple[SourceContext, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Variance in generation is the one thing a plan-pinned pipeline cannot absorb."""
    monkeypatch.setenv("LITELLM_BASE_URL", "http://gateway.invalid")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "key")
    monkeypatch.setenv("LITELLM_LLM_MODEL", "snp-llm")
    captured: dict[str, object] = {}

    def _urlopen(request: object, timeout: float = 0) -> object:
        captured["body"] = json.loads(request.data)  # type: ignore[attr-defined]
        return io.BytesIO(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "summary": "A grounded sentence.",
                                        "specifications": ["A grounded claim."],
                                    }
                                )
                            }
                        }
                    ]
                }
            ).encode("utf-8")
        )

    monkeypatch.setattr(urllib.request, "urlopen", MagicMock(side_effect=_urlopen))
    generate_page_body("Acme", retrieved)

    assert captured["body"]["temperature"] == 0  # type: ignore[index]
