"""Literature analysis: keyphrases, TF-IDF similarity, themes, gap detection.

Pure-numpy text machinery — small corpus scale, fully deterministic.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Sequence

import numpy as np

_STOPWORDS = frozenset("""
a an the and or but if then else of for from in on at to by with without within
is are was were be been being it its this that these those we our their his her
your not no nor as into over under about between among during before after above
below up down out off again further once here there all any both each few more
most other some such only own same so than too very can will just should now
using based paper propose proposed approach method methods results show shows
study problem problems model models algorithm algorithms
""".split())

_TOKEN_RE = re.compile(r"[a-z][a-z0-9-]{1,}")


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 2]


# ---------------------------------------------------------------------------
def extract_keyphrases(text: str, top_k: int = 8) -> list[str]:
    """Most salient unigram + bigram keyphrases of a text."""
    tokens = _tokenize(text)
    if not tokens:
        return []
    counts: Counter[str] = Counter(tokens)
    bigrams: Counter[str] = Counter(zip(tokens, tokens[1:]))
    phrases: list[tuple[float, str]] = []
    for tok, n in counts.items():
        phrases.append((n * math.log(1 + len(tok)), tok))
    for (a, b), n in bigrams.items():
        phrases.append((n * 2.0, f"{a} {b}"))
    phrases.sort(key=lambda p: -p[0])
    out: list[str] = []
    seen: set[str] = set()
    for _, phrase in phrases:
        if phrase in seen:
            continue
        seen.add(phrase)
        out.append(phrase)
        if len(out) >= top_k:
            break
    return out


# ---------------------------------------------------------------------------
class TfidfSpace:
    """TF-IDF vectors with cosine similarity over a fixed document set."""

    def __init__(self, documents: Sequence[str]):
        token_lists = [_tokenize(d) for d in documents]
        self.vocab: dict[str, int] = {}
        for toks in token_lists:
            for t in toks:
                if t not in self.vocab:
                    self.vocab[t] = len(self.vocab)
        n_docs = len(documents)
        df = np.zeros(len(self.vocab))
        rows: list[np.ndarray] = []
        for toks in token_lists:
            row = np.zeros(len(self.vocab))
            for t in toks:
                row[self.vocab[t]] += 1.0
            df[row > 0] += 1
            rows.append(row)
        idf = np.log((1 + n_docs) / (1 + df)) + 1.0
        self.matrix = np.stack(rows) * idf
        norms = np.linalg.norm(self.matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.matrix = self.matrix / norms
        self.idf = idf

    def vectorize_query(self, query: str) -> np.ndarray:
        vec = np.zeros(len(self.vocab))
        for t in _tokenize(query):
            if t in self.vocab:
                vec[self.vocab[t]] += 1.0
        vec *= self.idf
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    def relevance(self, query: str) -> np.ndarray:
        q = self.vectorize_query(query)
        return self.matrix @ q

    def pairwise_similarity(self) -> np.ndarray:
        return self.matrix @ self.matrix.T


# ---------------------------------------------------------------------------
def organize_themes(documents: Sequence[str], k: int = 4,
                    seed: int = 13) -> list[dict[str, Any]]:
    """Cluster documents into k themes; label each theme by its top phrases.

    Spherical k-means on TF-IDF vectors (cosine geometry), deterministic seed.
    """
    docs = [d for d in documents]
    n = len(docs)
    k = max(1, min(k, n))
    space = TfidfSpace(docs)
    X = space.matrix
    rng = np.random.default_rng(seed)

    centroids = X[rng.choice(n, size=k, replace=False)].copy()
    assign = np.zeros(n, dtype=int)
    for _ in range(30):
        sims = X @ centroids.T
        new_assign = np.argmax(sims, axis=1)
        if np.array_equal(new_assign, assign):
            break
        assign = new_assign
        for ci in range(k):
            members = X[assign == ci]
            if len(members) == 0:
                centroids[ci] = X[rng.integers(n)]
            else:
                c = members.mean(axis=0)
                norm = np.linalg.norm(c)
                centroids[ci] = c / norm if norm > 0 else X[rng.integers(n)]
    inv_vocab = {v: t for t, v in space.vocab.items()}
    themes = []
    for ci in range(k):
        member_idx = [i for i in range(n) if assign[i] == ci]
        centroid = centroids[ci]
        top_terms = sorted(range(len(centroid)),
                           key=lambda j: -centroid[j])[:5]
        labels = [inv_vocab[j] for j in top_terms if centroid[j] > 0]
        themes.append({
            "theme_id": ci,
            "member_indices": member_idx,
            "labels": labels,
        })
    return themes


# ---------------------------------------------------------------------------
def identify_gaps(question: str, documents: Sequence[str],
                  min_gap_terms: int = 2) -> list[dict[str, Any]]:
    """Terms central to the research question that the corpus barely covers.

    A 'gap' is a question keyword whose document frequency is far below its
    frequency-weighted importance in the question itself.
    """
    q_tokens = [t for t in _tokenize(question)]
    q_counts = Counter(q_tokens)
    doc_token_sets = []
    for d in documents:
        s = set(_tokenize(d))
        doc_token_sets.append(s)
    gaps = []
    for term, q_freq in q_counts.most_common(15):
        covered = sum(1 for s in doc_token_sets if term in s)
        coverage = covered / max(1, len(documents))
        importance = q_freq
        if coverage < 0.25 and importance >= 1 and len(term) > 3:
            gaps.append({
                "term": term,
                "question_frequency": importance,
                "corpus_coverage": round(coverage, 3),
            })
    gaps.sort(key=lambda g: (-g["question_frequency"], g["corpus_coverage"]))
    merged: list[dict[str, Any]] = []
    used = set()
    for g in gaps:
        if any(g["term"] in u or u in g["term"] for u in used):
            continue
        used.add(g["term"])
        merged.append(g)
        if len(merged) >= 5:
            break
    # Only report genuine gaps when enough distinct weak terms exist.
    if sum(1 for g in merged if g["corpus_coverage"] < 0.25) < min(min_gap_terms, len(merged)):
        return []
    return merged


def compare_sources(space_a: TfidfSpace, titles_a: Sequence[str]) -> list[dict[str, Any]]:
    """Top similar source pairs for cross-comparison tables."""
    sims = space_a.pairwise_similarity()
    pairs = []
    n = len(titles_a)
    for i in range(n):
        for j in range(i + 1, n):
            pairs.append({"a": i, "b": j, "similarity": float(sims[i, j]),
                          "title_a": titles_a[i], "title_b": titles_a[j]})
    pairs.sort(key=lambda p: -p["similarity"])
    return pairs[:6]
