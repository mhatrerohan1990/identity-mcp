"""Keyword search over the question bank (BM25), the second half of hybrid retrieval.

Embeddings match *meaning* loosely: with ``nomic-embed-text`` nearly every pair of bank questions scores
between 0.5 and 0.8, so the ranking is only weakly informative. Keyword search is the opposite: it is blind to
paraphrase but precise about the specific words that distinguish one entry from another ("terminated",
"MFA", "Snowflake", "dormant"). ``retrieval.py`` fuses the two rankings.

This module knows nothing about embeddings, the database or the model. It turns text into normalized tokens
and scores a query against a fixed list of documents with Okapi BM25.

Normalization (applied identically to documents and queries, so they stay comparable):

1. lower-case and split into alphanumeric words;
2. drop stop words (question words and filler such as "how", "many", "show", "the");
3. rewrite a few multi-word phrases (``two-factor`` -> ``mfa``) and map domain synonyms to one canonical word (``left``/``departed`` -> ``terminated``, ``2fa`` -> ``mfa``);
4. strip a plural ``s`` (``applications`` -> ``application``), except on words like ``status`` or ``access``.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

import numpy as np

K1 = 1.5  # BM25 term-frequency saturation: how quickly repeated words stop adding score
B = 0.75  # BM25 length normalization: 0 ignores document length, 1 fully normalizes it

STOP_WORDS = frozenset("""
a an and are any as at be been by can could did do does for from get give has have how i if in is it its list me
my of on or our show tell that the their them there these they this to us was we were what when where which who
whom whose will with would you your all each every many much more most than then also about info information
find display please
""".split())

# Different words people use for what the bank (and the data) calls one thing.
SYNONYMS = {
    "left": "terminated", "leaver": "terminated", "leavers": "terminated", "departed": "terminated",
    "offboarded": "terminated", "fired": "terminated", "former": "terminated", "ex": "terminated",
    "2fa": "mfa",
    "administrator": "admin", "administrators": "admin", "superadmin": "admin",
    "inactive": "dormant", "idle": "dormant", "unused": "dormant",
    "employee": "user", "employees": "user", "people": "user", "person": "user", "staff": "user",
    "accounts": "user", "account": "user", "member": "user", "members": "user",
    "app": "application", "apps": "application",
    "team": "department", "teams": "department",
    "critical": "criticality", "reach": "access", "permission": "access", "permissions": "access", "rights": "access", "privilege": "access",
}
# Multi-word phrases that must be rewritten before the text is split into words.
PHRASES = (
    (re.compile(r"\b(?:two|multi)[- ]?factor(?: authentication)?\b"), "mfa"),
    (re.compile(r"\bex[- ]employees?\b"), "terminated user"),
)
_NO_PLURAL_STRIP = ("ss", "us", "is")  # access, status, analysis


def tokenize(text: str) -> list[str]:
    """Normalize `text` into the tokens that are indexed and searched (see the module docstring for the steps)."""
    text = text.lower()
    for pattern, replacement in PHRASES:
        text = pattern.sub(replacement, text)
    words = re.findall(r"[a-z0-9]+", text)
    tokens = []
    for word in words:
        if word in STOP_WORDS:
            continue
        word = SYNONYMS.get(word, word)
        if len(word) > 3 and word.endswith("s") and not word.endswith(_NO_PLURAL_STRIP):
            word = word[:-1]
        word = SYNONYMS.get(word, word)  # a synonym may only match once the plural is stripped
        if word not in STOP_WORDS:
            tokens.append(word)
    return tokens


@dataclass(frozen=True)
class LexicalIndex:
    """A BM25 index over a fixed list of documents. Build it with :func:`build`; query it with :meth:`scores`.

    Attributes:
        term_counts: For each document, how many times each token occurs in it.
        lengths: Token count of each document.
        idf: Inverse document frequency of each token: rare tokens weigh more than common ones.
        average_length: Mean document length, used for length normalization.
    """

    term_counts: list[Counter]
    lengths: np.ndarray
    idf: dict[str, float]
    average_length: float

    def scores(self, query: str) -> np.ndarray:
        """BM25 score of every document for `query`, in document order.

        A document that shares no token with the query scores 0, so ``scores > 0`` means "matched at least one
        word". Scores are only comparable within one query; they are not bounded by 1.
        """
        out = np.zeros(len(self.term_counts))
        for token in set(tokenize(query)):
            idf = self.idf.get(token)
            if idf is None:
                continue
            for i, counts in enumerate(self.term_counts):
                tf = counts.get(token, 0)
                if tf:
                    norm = K1 * (1 - B + B * self.lengths[i] / self.average_length)
                    out[i] += idf * tf * (K1 + 1) / (tf + norm)
        return out


def build(documents: list[str]) -> LexicalIndex:
    """Index `documents` (one string each). An empty list gives an index that scores nothing."""
    term_counts = [Counter(tokenize(d)) for d in documents]
    lengths = np.array([sum(c.values()) for c in term_counts], dtype=float)
    n = len(documents)
    containing = Counter(token for counts in term_counts for token in counts)
    # the "+1" inside the log keeps idf positive even for a word that appears in every document
    idf = {t: math.log(1 + (n - df + 0.5) / (df + 0.5)) for t, df in containing.items()}
    return LexicalIndex(term_counts, lengths, idf, float(lengths.mean()) if n and lengths.sum() else 1.0)
