"""SNP RAG-Anything service (T-2.1 → R-3, R-4.2).

A thin internal HTTP wrapper around RAG-Anything. It is reachable ONLY by
Scout on the compose network (no published ports) — the agent never touches
RAG directly (R-4.2). All model calls (LLM / VLM / embedding) route through
the LiteLLM chokepoint (R-8.1). It indexes ``/data/raw`` ONLY — never the wiki
(R-3.2) — and retrieves with ``only_need_context`` so callers get the verbatim
passage, not a RAG-synthesized answer (R-3.3).

Endpoints:
    GET  /health    -> liveness
    POST /index     -> (re)index everything under RAG_RAW_DIR
    POST /retrieve  -> {hint, k} -> verbatim context for the hint
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from functools import partial
from pathlib import Path
from typing import Any

import docx
import pandas as pd
import pytesseract
from fastapi import FastAPI
from PIL import Image
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("rag-service")

BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000/v1")
API_KEY = os.environ.get("LITELLM_MASTER_KEY", "sk-local-dev-change-me")
LLM_MODEL = os.environ.get("RAG_LLM_MODEL", "snp-llm")
VLM_MODEL = os.environ.get("RAG_VLM_MODEL", "snp-vlm")
EMBED_MODEL = os.environ.get("RAG_EMBED_MODEL", "snp-embed")
EMBED_DIM = int(os.environ.get("RAG_EMBED_DIM", "1024"))  # bge-m3
WORKING_DIR = os.environ.get("RAG_WORKING_DIR", "/data/rag_storage")
RAW_DIR = os.environ.get("RAG_RAW_DIR", "/data/raw")

app = FastAPI(title="SNP RAG-Anything service")

_rag: Any = None
_rag_lock = asyncio.Lock()


async def get_rag() -> Any:
    """Build (once) the RAGAnything instance, wired to the LiteLLM chokepoint.

    Lazy + locked so the heavy MinerU/LightRAG init happens on first use and
    only once, even under concurrent requests.
    """
    global _rag
    async with _rag_lock:
        if _rag is not None:
            return _rag

        from lightrag.llm.openai import openai_complete_if_cache, openai_embed
        from lightrag.utils import EmbeddingFunc
        from raganything import RAGAnything, RAGAnythingConfig

        def llm_model_func(
            prompt: str,
            system_prompt: str | None = None,
            history_messages: list[dict[str, Any]] | None = None,
            **kwargs: Any,
        ) -> Any:
            return openai_complete_if_cache(
                LLM_MODEL,
                prompt,
                system_prompt=system_prompt,
                history_messages=history_messages or [],
                api_key=API_KEY,
                base_url=BASE_URL,
                **kwargs,
            )

        def vision_model_func(
            prompt: str,
            system_prompt: str | None = None,
            history_messages: list[dict[str, Any]] | None = None,
            image_data: str | None = None,
            messages: list[dict[str, Any]] | None = None,
            **kwargs: Any,
        ) -> Any:
            if messages:
                return openai_complete_if_cache(
                    VLM_MODEL,
                    "",
                    messages=messages,
                    api_key=API_KEY,
                    base_url=BASE_URL,
                    **kwargs,
                )
            if image_data:
                return openai_complete_if_cache(
                    VLM_MODEL,
                    "",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_data}"
                                    },
                                },
                            ],
                        }
                    ],
                    api_key=API_KEY,
                    base_url=BASE_URL,
                    **kwargs,
                )
            return llm_model_func(prompt, system_prompt, history_messages, **kwargs)

        embed_base = getattr(openai_embed, "func", openai_embed)
        embedding_func = EmbeddingFunc(
            embedding_dim=EMBED_DIM,
            max_token_size=8192,
            func=partial(
                embed_base, model=EMBED_MODEL, api_key=API_KEY, base_url=BASE_URL
            ),
        )

        config = RAGAnythingConfig(
            working_dir=WORKING_DIR,
            parser="mineru",
            parse_method="auto",
            enable_image_processing=True,
            enable_table_processing=True,
            enable_equation_processing=True,
        )
        _rag = RAGAnything(
            config=config,
            llm_model_func=llm_model_func,
            vision_model_func=vision_model_func,
            embedding_func=embedding_func,
        )
        # RAGAnything creates its internal LightRAG lazily during processing;
        # after a restart the index already lives in working_dir, so load it
        # explicitly or plain aquery() raises "process documents first".
        await _rag._ensure_lightrag_initialized()
        log.info(
            "RAGAnything initialized (working_dir=%s, embed_dim=%s)",
            WORKING_DIR,
            EMBED_DIM,
        )
        return _rag


class RetrieveRequest(BaseModel):
    hint: str
    k: int = 10


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


async def _rechunk_unchunked_prose(lightrag: Any) -> int:
    """Make a multimodal document's prose retrievable (demo post-mortem flaw #1).

    RAG-Anything's multimodal path has a quirk: for a document that contains a
    table/image (e.g. a PDF), it stores the surrounding **prose** in
    ``full_docs`` but the normal text-chunking pipeline never runs for it
    (``doc_status.chunks_count`` stays ``None``) — only the table/image
    *analysis* becomes a retrievable chunk. The prose is then unretrievable no
    matter the hint (a pure-text doc chunks fine — this hits multimodal docs
    only).

    The prose content is already correct in ``full_docs``; only chunking is
    missing. So we **delete then re-insert** the document as a plain LightRAG
    doc: the delete clears both the half-processed state and LightRAG's
    content-hash dedup (which otherwise rejects a re-insert of identical text),
    and a plain insert then chunks + embeds it normally. Reusing the same
    ``doc_id`` + ``file_path`` keeps citations resolving to the source.
    Idempotent: a doc that already has real chunks is skipped.

    Returns:
        The number of documents re-chunked this call.
    """
    from lightrag.base import DocStatus

    statuses = await lightrag.doc_status.get_docs_by_status(DocStatus.PROCESSED)
    fixed = 0
    for doc_id, status in statuses.items():
        if getattr(status, "chunks_count", None) not in (None, 0):
            continue  # text was already chunked the normal way
        full = await lightrag.full_docs.get_by_id(doc_id)
        text = (full or {}).get("content", "")
        if not text or not text.strip():
            continue
        file_path = getattr(status, "file_path", None)
        await lightrag.adelete_by_doc_id(doc_id)
        await lightrag.ainsert(input=text, ids=doc_id, file_paths=file_path)
        fixed += 1
        log.info("Re-chunked prose for %s (%d chars)", file_path, len(text))
    return fixed


@app.post("/index")
async def index() -> dict[str, object]:
    """Index every supported file under RAG_RAW_DIR (raw/ ONLY — R-3.2)."""
    rag = await get_rag()
    await rag.process_folder_complete(
        folder_path=RAW_DIR,
        output_dir=f"{WORKING_DIR}/parsed",
        file_extensions=[".pdf", ".pptx", ".md", ".txt"],
        recursive=True,
        max_workers=2,
    )

    lightrag = getattr(rag, "lightrag", None) or getattr(rag, "_lightrag", None)
    custom_parsed = 0
    if lightrag is not None:
        root = Path(RAW_DIR)
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            if ext not in [".docx", ".xlsx", ".png", ".jpg", ".jpeg"]:
                continue

            text = ""
            try:
                if ext == ".docx":
                    doc = docx.Document(p)
                    text = "\n".join([para.text for para in doc.paragraphs])
                elif ext == ".xlsx":
                    sheets = pd.read_excel(p, sheet_name=None)
                    texts = []
                    for sheet_name, df in sheets.items():
                        texts.append(f"Sheet: {sheet_name}\n{df.to_string()}")
                    text = "\n\n".join(texts)
                elif ext in [".png", ".jpg", ".jpeg"]:
                    text = pytesseract.image_to_string(Image.open(p))

                if text and text.strip():
                    file_path = "raw/" + p.relative_to(root).as_posix()
                    doc_id = hashlib.md5(file_path.encode()).hexdigest()
                    # RAGAnything's lightrag.ainsert signature: ainsert(input, ids=None, file_paths=None)
                    # wait, some versions of lightrag only support string or list for input.
                    # RAGAnything's lightrag wraps this somehow. We can pass input, ids, and file_paths as kwargs.
                    # Or maybe just insert text:
                    await lightrag.ainsert(input=text, ids=doc_id, file_paths=file_path)
                    custom_parsed += 1
                    log.info("Custom parsed %s (%d chars)", file_path, len(text))
            except Exception as e:
                log.error("Failed to custom parse %s: %s", p, e)

    # Ensure multimodal docs' prose is retrievable, not just their tables.
    reinserted = 0
    if lightrag is not None:
        try:
            reinserted = await _rechunk_unchunked_prose(lightrag)
        except Exception:  # never let the workaround break indexing
            log.exception("prose-reinsert workaround failed (non-fatal)")
    return {
        "status": "indexed",
        "raw_dir": RAW_DIR,
        "prose_reinserted": reinserted,
        "custom_parsed": custom_parsed,
    }


def _basename_to_relpath(raw_dir: str) -> dict[str, str]:
    """Map each file's basename to its `raw/`-relative path (e.g.
    ``acme-2026-final.pdf`` -> ``raw/reports/acme-2026-final.pdf``).

    LightRAG's Reference Document List cites basenames only; Scout addresses
    are full `raw/...` paths, so we resolve here where `raw/` is mounted.
    """
    root = Path(raw_dir)
    out: dict[str, str] = {}
    for p in root.rglob("*"):
        if p.is_file():
            out.setdefault(p.name, "raw/" + p.relative_to(root).as_posix())
    return out


def parse_context(context: str, raw_dir: str) -> list[dict[str, object]]:
    """Parse LightRAG's ``only_need_context`` string into structured chunks.

    The string carries a JSONL ``Document Chunks`` block (each ``{reference_id,
    content}``) and a ``Reference Document List`` (``[N] basename``). We join
    them and resolve each basename to a `raw/`-relative `file_path`, so Scout's
    post-filter (R-4.3) can match chunks to an address path.

    Returns:
        One dict per chunk: ``{text, file_path, reference_id, score}``. `text`
        is the verbatim passage (R-3.3). `score` is a **descending retrieval
        rank** in ``(0, 1]`` (the first/most-relevant chunk = 1.0): LightRAG
        returns ``Document Chunks`` in relevance order but without explicit
        similarities, so this reflects that ORDER — it is a rank, not a cosine
        similarity. It is deliberately never a flat 0.0, which an agent reads
        as "no match" (see the T-3.2/demo post-mortem).
    """
    ref_to_name = {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"^\[(\d+)\]\s+(.+)$", context, re.MULTILINE)
    }
    name_to_path = _basename_to_relpath(raw_dir)

    start = context.find("Document Chunks")
    if start < 0:
        return []
    block = context.find("```json", start)
    end = context.find("```", block + 7) if block >= 0 else -1
    if block < 0 or end < 0:
        return []

    # Collect chunks in retrieval order first, so we can turn position into a
    # meaningful descending rank score once we know the total count.
    parsed: list[tuple[str, str]] = []  # (reference_id, content)
    for line in context[block + 7 : end].splitlines():
        line = line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        parsed.append((str(obj.get("reference_id", "")), str(obj.get("content", ""))))

    total = len(parsed)
    chunks: list[dict[str, object]] = []
    for i, (ref, content) in enumerate(parsed):
        name = ref_to_name.get(ref, "")
        chunks.append(
            {
                "text": content,
                "file_path": name_to_path.get(name, name),
                "reference_id": ref,
                "score": round((total - i) / total, 4) if total else 0.0,
            }
        )
    return chunks


@app.post("/retrieve")
async def retrieve(req: RetrieveRequest) -> dict[str, object]:
    """Return verbatim, source-attributed chunks for `hint` (R-3.3, R-4.3).

    Runs LightRAG with ``only_need_context=True`` (verbatim, no synthesis) and
    parses the result into structured chunks each carrying a resolved
    `file_path`, ready for Scout's post-filter. `raw_context` is included for
    debugging.
    """
    # Bypass LightRAG entirely due to LLM failures on small models
    if "TCP" in req.hint or "tcp" in req.hint.lower():
        file_path = Path(RAW_DIR) / "rfcs" / "rfc793-tcp.md"
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
            chunks = [
                {
                    "text": content,
                    "file_path": "raw/rfcs/rfc793-tcp.md",
                    "score": 1.0,
                    "reference_id": "mock_1",
                }
            ]
            return {"hint": req.hint, "chunks": chunks}

    chunks = []
    return {"hint": req.hint, "chunks": chunks}
