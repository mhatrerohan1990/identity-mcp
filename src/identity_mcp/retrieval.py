"""Retrieve the bank questions most similar to a user's question (the RAG step).

Each bank question is embedded once and cached on disk; a query embeds the user's question and
ranks the bank by cosine similarity. Examples are used as few-shot context for SQL generation.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import bank, llm

K = int(os.environ.get("IDENTITY_MCP_EXAMPLES_K", "4"))
MIN_SCORE = float(os.environ.get("IDENTITY_MCP_EXAMPLES_MIN_SCORE", "0.5"))
DOC_PREFIX, QUERY_PREFIX = "search_document: ", "search_query: "  # required by nomic-embed-text

_index: tuple[tuple, list[bank.Example], np.ndarray] | None = None


@dataclass(frozen=True)
class Hit:
    example: bank.Example
    score: float


def _cache_file() -> Path:
    return Path(os.environ.get("IDENTITY_MCP_CACHE_DIR", ".cache")) / "bank_embeddings.json"


def _normalize(m: np.ndarray) -> np.ndarray:
    return m / np.maximum(np.linalg.norm(m, axis=-1, keepdims=True), 1e-12)


async def _document_vectors(texts: list[str]) -> list[list[float]]:
    """Embeddings for bank questions, read from the disk cache where the text and model are unchanged."""
    path = _cache_file()
    cache: dict[str, list[float]] = json.loads(path.read_text()) if path.exists() else {}
    keys = [hashlib.sha256(f"{llm.EMBED_MODEL}|{t}".encode()).hexdigest() for t in texts]
    missing = [i for i, k in enumerate(keys) if k not in cache]
    if missing:
        vectors = await llm.embed([DOC_PREFIX + texts[i] for i in missing])
        for i, v in zip(missing, vectors):
            cache[keys[i]] = v
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({k: cache[k] for k in keys}))  # also drops entries for removed questions
    return [cache[k] for k in keys]


async def get_index() -> tuple[list[bank.Example], np.ndarray]:
    global _index
    path = bank.bank_path()
    stamp = (str(path), path.stat().st_mtime_ns if path.exists() else 0, llm.EMBED_MODEL)
    if _index is None or _index[0] != stamp:
        examples = bank.load(path)
        matrix = _normalize(np.array(await _document_vectors([e.question for e in examples]), dtype=np.float32))
        _index = (stamp, examples, matrix)
    return _index[1], _index[2]


async def retrieve(
    question: str, k: int | None = None, min_score: float | None = None, exclude: frozenset[str] | set[str] = frozenset()
) -> list[Hit]:
    """The `k` most similar bank entries (best first) scoring at least `min_score`, skipping ids in `exclude`."""
    k = K if k is None else k
    min_score = MIN_SCORE if min_score is None else min_score
    if k <= 0:
        return []
    examples, matrix = await get_index()
    q = _normalize(np.array((await llm.embed([QUERY_PREFIX + question]))[0], dtype=np.float32))
    scores = matrix @ q
    hits = [
        Hit(examples[i], float(scores[i]))
        for i in np.argsort(-scores)
        if examples[i].id not in exclude and scores[i] >= min_score
    ]
    return hits[:k]
