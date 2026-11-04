"""Retrieve the bank questions most similar to a user's question (the RAG step).

Two rankings of the bank are computed and fused:

* **dense** - each bank question is embedded once (cached on disk); the user's question is embedded and the bank is
  ranked by cosine similarity. Good at paraphrase, weak at telling similar entries apart.
* **lexical** - BM25 keyword search over question + tags (``lexical.py``). Blind to paraphrase, precise about the
  words that matter ("terminated", "MFA", "Snowflake").

They are merged with reciprocal rank fusion: an entry scores ``1 / (60 + rank)`` in each list that contains it, and
the scores are added. Only ranks are used, so the two score scales never have to be reconciled. The best ``k``
become few-shot examples for SQL generation. ``IDENTITY_MCP_RETRIEVAL`` selects ``dense`` (default), ``hybrid`` or
``lexical``. Dense is the default because on ``evals.retrieval_check`` it found a reworded question's source entry
at least as well as hybrid (recall@4 0.99 for both, MRR 0.95 against 0.92); hybrid is kept so it can be compared on
the held-out check and promoted only if it wins there.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import bank, lexical, llm
from .logs import flow

K = int(os.environ.get("IDENTITY_MCP_EXAMPLES_K", "4"))
MIN_SCORE = float(os.environ.get("IDENTITY_MCP_EXAMPLES_MIN_SCORE", "0.5"))
DOC_PREFIX, QUERY_PREFIX = "search_document: ", "search_query: "  # required by nomic-embed-text
MODES = ("dense", "hybrid", "lexical")
RRF_K = 60  # reciprocal rank fusion constant: larger values flatten the difference between rank 1 and rank 5

_index: tuple[tuple, list[bank.Example], np.ndarray] | None = None
_lexical: tuple[int, lexical.LexicalIndex] | None = None


def mode() -> str:
    """The retrieval strategy: ``$IDENTITY_MCP_RETRIEVAL`` (``dense``, ``hybrid`` or ``lexical``), default ``dense``."""
    value = os.environ.get("IDENTITY_MCP_RETRIEVAL", "dense").strip().lower()
    return value if value in MODES else "dense"


@dataclass(frozen=True)
class Hit:
    """A retrieved bank example and how well it matched.

    Attributes:
        example: The bank entry.
        score: The ranking score (higher is better). In ``dense`` mode it is the cosine similarity (0 to 1); in
            ``lexical`` mode the BM25 score; in ``hybrid`` mode the fused score scaled so a first place in both
            rankings is 1.0. Only comparable within one result list.
        dense: Cosine similarity to the question, or None if the dense ranking was not used for this entry.
        lexical: BM25 score, or None if the keyword ranking did not match this entry.
    """
    example: bank.Example
    score: float
    dense: float | None = None
    lexical: float | None = None


def _cache_file() -> Path:
    """Path of the embedding cache: ``bank_embeddings.json`` under ``$IDENTITY_MCP_CACHE_DIR`` (default ``.cache``).
    """
    return Path(os.environ.get("IDENTITY_MCP_CACHE_DIR", ".cache")) / "bank_embeddings.json"


def _normalize(m: np.ndarray) -> np.ndarray:
    """Scale each row (vector) of `m` to length 1, so a dot product between rows equals cosine similarity."""
    return m / np.maximum(np.linalg.norm(m, axis=-1, keepdims=True), 1e-12)


async def _document_vectors(texts: list[str]) -> list[list[float]]:
    """Embeddings for bank questions, read from the disk cache where the text and model are unchanged."""
    path = _cache_file()
    cache: dict[str, list[float]] = json.loads(path.read_text()) if path.exists() else {}
    keys = [hashlib.sha256(f"{llm.EMBED_MODEL}|{t}".encode()).hexdigest() for t in texts]
    missing = [i for i, k in enumerate(keys) if k not in cache]
    if missing:
        flow.info("embedding %d of %d bank question(s) (rest cached) with %s", len(missing), len(texts), llm.EMBED_MODEL)
        vectors = await llm.embed([DOC_PREFIX + texts[i] for i in missing])
        for i, v in zip(missing, vectors):
            cache[keys[i]] = v
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({k: cache[k] for k in keys}))  # also drops entries for removed questions
    return [cache[k] for k in keys]


async def get_index() -> tuple[list[bank.Example], np.ndarray]:
    """The in-memory search index: the bank examples and their unit-length embedding matrix.

    Built on first use and rebuilt automatically when the bank file's modification time or the embedding model
    changes. The embeddings themselves come from the on-disk cache, so only new or edited questions cost a model call.
    """
    global _index
    path = bank.bank_path()
    stamp = (str(path), path.stat().st_mtime_ns if path.exists() else 0, llm.EMBED_MODEL)
    if _index is None or _index[0] != stamp:
        examples = bank.load(path)
        matrix = _normalize(np.array(await _document_vectors([e.question for e in examples]), dtype=np.float32))
        _index = (stamp, examples, matrix)
    return _index[1], _index[2]


def get_lexical(examples: list[bank.Example]) -> lexical.LexicalIndex:
    """The keyword index over `examples` (question + tags), cached until the examples change.

    Built from the list the caller already holds, so its scores line up position-for-position with that list and
    with the embedding matrix. Needs no model and is fast (about a hundred short documents).
    """
    global _lexical
    key = hash(tuple((e.id, e.question, e.tags) for e in examples))
    if _lexical is None or _lexical[0] != key:
        _lexical = (key, lexical.build([f"{e.question} {' '.join(e.tags)}" for e in examples]))
    return _lexical[1]


def _fuse(rankings: list[list[int]]) -> dict[int, float]:
    """Reciprocal rank fusion: each ranking (best first, as example positions) adds ``1 / (RRF_K + rank)`` per entry."""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, i in enumerate(ranking, start=1):
            fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank)
    return fused


async def retrieve(
    question: str, k: int | None = None, min_score: float | None = None, exclude: frozenset[str] | set[str] = frozenset()
) -> list[Hit]:
    """The `k` best bank entries for `question`, best first, skipping ids in `exclude`.

    Args:
        question: The user's question.
        k: How many to return; default ``$IDENTITY_MCP_EXAMPLES_K`` (4). 0 returns nothing without calling any model.
        min_score: Minimum cosine similarity for an entry to enter the *dense* ranking; default
            ``$IDENTITY_MCP_EXAMPLES_MIN_SCORE`` (0.5). It does not apply to the keyword ranking, which already
            requires at least one shared word.
        exclude: Bank ids to hide (used by evals to hold a question's own entry out).

    Returns:
        Up to `k` hits. Empty if nothing matched either ranking.

    Raises:
        llm.LLMError: if the embedding model is needed (``hybrid``/``dense``) and unavailable.
    """
    k = K if k is None else k
    min_score = MIN_SCORE if min_score is None else min_score
    if k <= 0:
        return []
    strategy = mode()
    dense_scores: np.ndarray | None = None
    lexical_scores: np.ndarray | None = None
    if strategy == "lexical":  # no embedding model needed: read the bank directly
        examples = bank.load()
    else:
        examples, matrix = await get_index()
        q = _normalize(np.array((await llm.embed([QUERY_PREFIX + question]))[0], dtype=np.float32))
        dense_scores = matrix @ q
    if strategy != "dense":
        lexical_scores = get_lexical(examples).scores(question)

    allowed = [i for i, e in enumerate(examples) if e.id not in exclude]
    rankings: list[list[int]] = []
    if dense_scores is not None:
        rankings.append(sorted((i for i in allowed if dense_scores[i] >= min_score), key=lambda i: -dense_scores[i]))
    if lexical_scores is not None:
        rankings.append(sorted((i for i in allowed if lexical_scores[i] > 0), key=lambda i: -lexical_scores[i]))
    fused = _fuse(rankings)
    best_possible = len(rankings) / (RRF_K + 1)  # first place in every ranking used

    hits = []
    for i in sorted(fused, key=lambda i: (-fused[i], i))[:k]:
        d = float(dense_scores[i]) if dense_scores is not None and dense_scores[i] >= min_score else None
        lx = float(lexical_scores[i]) if lexical_scores is not None and lexical_scores[i] > 0 else None
        if strategy == "dense":
            score = d
        elif strategy == "lexical":
            score = lx
        else:
            score = fused[i] / best_possible
        hits.append(Hit(examples[i], float(score), d, lx))
    flow.debug("retrieval mode=%s k=%d hits=%s", strategy, k, [(h.example.id, round(h.score, 3)) for h in hits])
    return hits
