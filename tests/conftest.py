import re
import zlib

import pytest

from identity_mcp import llm, retrieval


def fake_vector(text: str, dims: int = 256) -> list[float]:
    """Bag-of-words hashing: texts sharing words are similar. Deterministic, no model needed."""
    v = [0.0] * dims
    for w in re.findall(r"[a-z0-9]+", text.lower().replace("search_query:", "").replace("search_document:", "")):
        v[zlib.crc32(w.encode()) % dims] += 1.0
    return v


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch, tmp_path):
    """Keep the suite independent of Ollama and of any real embedding cache."""
    async def embed(texts, *, timeout=60):
        return [fake_vector(t) for t in texts]

    monkeypatch.setattr(llm, "embed", embed)
    monkeypatch.setenv("IDENTITY_MCP_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(retrieval, "_index", None)
