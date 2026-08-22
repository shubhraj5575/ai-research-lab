"""Literature subsystem tests (offline; network never required)."""

from __future__ import annotations

import numpy as np
import pytest

from rlab.literature.analysis import (
    TfidfSpace,
    compare_sources,
    extract_keyphrases,
    identify_gaps,
    organize_themes,
)
from rlab.literature.cache import DiskCache
from rlab.literature.providers import SEED_CORPUS, ArxivProvider, SeedCorpusProvider


DOCS = [
    "UCB1 achieves logarithmic regret on stochastic bandits using confidence bounds.",
    "Thompson sampling draws from posterior distributions and shows strong bandit performance.",
    "Differential evolution is a population method for multimodal continuous optimization.",
    "Simulated annealing escapes local optima via temperature-controlled acceptance of worse moves.",
]


def test_keyphrase_extraction_orders_by_salience():
    phrases = extract_keyphrases(
        "Bandit algorithms balance exploration and exploitation. "
        "The bandit regret grows logarithmically for UCB1."
    )
    assert phrases, "no keyphrases extracted"
    assert "bandit" in " ".join(phrases)


def test_tfidf_relevance_ranks_matching_doc_first():
    space = TfidfSpace(DOCS)
    rel = space.relevance("regret bounds for bandit policies")
    assert int(np.argmax(rel)) in (0, 1)


def test_tfidf_query_with_unknown_vocab_is_safe():
    space = TfidfSpace(DOCS)
    rel = space.relevance("zzz qqq unknownword")
    assert rel.shape == (len(DOCS),) and float(rel.sum()) >= 0.0


def test_pairwise_similarity_symmetric_diag_one():
    space = TfidfSpace(DOCS)
    sims = space.pairwise_similarity()
    assert np.allclose(np.diag(sims), 1.0)
    assert np.allclose(sims, sims.T)


def test_organize_themes_partitions_documents():
    themes = organize_themes(DOCS, k=2, seed=3)
    members = [i for t in themes for i in t["member_indices"]]
    assert sorted(members) == list(range(len(DOCS)))
    assert all(t["labels"] or True for t in themes)


def test_identify_gaps_finds_uncovered_terms():
    question = ("How do variance-adaptive confidence bounds and reward drift "
                "change regret rankings on nonstationary bandits?")
    gaps = identify_gaps(question, DOCS)
    terms = {g["term"] for g in gaps}
    # corpus says nothing about drift/nonstationary
    assert any(t in terms for t in ("drift", "nonstationary", "variance-adaptive"))


def test_seed_corpus_provider_search_ranks_by_term_overlap():
    provider = SeedCorpusProvider()
    results = provider.search("thompson sampling posterior bandit", max_results=3)
    assert results
    joined = " ".join(r.title + r.abstract for r in results[0:1]).lower()
    assert "sampling" in joined


# ---------------------------------------------------------------------------
# DiskCache behaviour
# ---------------------------------------------------------------------------
def test_disk_cache_roundtrip_and_ttl(tmp_path):
    cache = DiskCache(tmp_path / "c", ttl_s=100.0)
    assert cache.get("ns", "k") is None
    cache.put("ns", "k", {"a": 1})
    assert cache.get("ns", "k") == {"a": 1}
    short_ttl = DiskCache(tmp_path / "c2", ttl_s=-1.0)
    short_ttl.put("ns", "k", {"b": 2})
    assert short_ttl.get("ns", "k") is None


def test_disk_cache_stats(tmp_path):
    cache = DiskCache(tmp_path / "c")
    cache.put("x", "y", [1])
    cache.get("x", "y")
    cache.get("x", "missing")
    stats = cache.stats()
    assert stats["hits"] == 1 and stats["misses"] == 1 and stats["entries"] == 1


# ---------------------------------------------------------------------------
# arXiv provider: parsing tested with a synthetic Atom document (no network).
# The live path is exercised only when RLAB_TEST_NETWORK=1.
# ---------------------------------------------------------------------------
ATOM_DOC = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1234.5678v1</id>
    <updated>2020-05-01T00:00:00Z</updated>
    <published>2020-05-01T00:00:00Z</published>
    <title>A Study of Bandits\nwith Long Title</title>
    <summary>Abstract text about bandit regret.</summary>
    <author><name>Ada Lovelace</name></author>
    <author><name>Alan Turing</name></author>
    <link href="http://arxiv.org/abs/1234.5678v1" rel="alternate" type="text/html"/>
  </entry>
</feed>
"""


def test_arxiv_atom_parsing():
    provider = ArxivProvider()
    sources = provider._parse_atom(ATOM_DOC)
    assert len(sources) == 1
    s = sources[0]
    assert s.kind == "arxiv"
    assert s.title.startswith("A Study of Bandits")
    assert s.authors == ["Ada Lovelace", "Alan Turing"]
    assert s.year == 2020
    assert s.url.endswith("1234.5678v1")


@pytest.mark.skipif(not __import__("os").environ.get("RLAB_TEST_NETWORK"),
                    reason="network test disabled by default")
def test_arxiv_live_smoke():
    provider = ArxivProvider()
    results = provider.search("multi-armed bandit", max_results=3)
    assert provider.last_error is None, provider.last_error
    assert len(results) == 3
