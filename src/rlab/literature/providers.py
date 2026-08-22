"""Source providers: arXiv (live) and a bundled seed corpus (offline).

The seed corpus contains accurate bibliographic references to classic papers
with short factual summaries written for this project. Entries are labeled
``kind="seed_corpus"`` everywhere they appear so offline sessions can never be
confused with freshly discovered web sources.
"""

from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .cache import DiskCache


@dataclass
class RawSource:
    kind: str                      # "arxiv" | "seed_corpus"
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    url: str | None = None
    abstract: str = ""


_ARXIV_NS = "{http://www.w3.org/2005/Atom}"


class ArxivProvider:
    """Queries the public arXiv Atom API (no key required).

    Respects arXiv's requested courtesy delay between requests.
    """

    kind = "arxiv"
    ENDPOINT = "https://export.arxiv.org/api/query"

    def __init__(self, cache: DiskCache | None = None, courtesy_delay_s: float = 3.0,
                 timeout_s: float = 25.0):
        self.cache = cache
        self.delay = courtesy_delay_s
        self.timeout = timeout_s
        self._last_request_ts = 0.0
        self.last_error: str | None = None

    def search(self, query: str, max_results: int = 10) -> list[RawSource]:
        cache_key = f"q={query!r};n={max_results}"
        if self.cache is not None:
            cached = self.cache.get("arxiv", cache_key)
            if cached is not None:
                return [RawSource(**item) for item in cached]
        params = urllib.parse.urlencode({
            "search_query": f'all:"{query}"',
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
        })
        url = f"{self.ENDPOINT}?{params}"
        wait = self.delay - (time.time() - self._last_request_ts)
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                body = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.last_error = f"arxiv fetch failed: {exc}"
            return []
        self._last_request_ts = time.time()
        try:
            sources = self._parse_atom(body)
        except ET.ParseError as exc:
            self.last_error = f"arxiv parse failed: {exc}"
            return []
        if self.cache is not None:
            self.cache.put("arxiv", cache_key,
                           [s.__dict__.copy() for s in sources])
        return sources

    def _parse_atom(self, body: bytes) -> list[RawSource]:
        root = ET.fromstring(body)
        out: list[RawSource] = []
        for entry in root.findall(f"{_ARXIV_NS}entry"):
            title = (entry.findtext(f"{_ARXIV_NS}title") or "").strip().replace("\n", " ")
            summary = (entry.findtext(f"{_ARXIV_NS}summary") or "").strip().replace("\n", " ")
            authors = [
                (a.findtext(f"{_ARXIV_NS}name") or "").strip()
                for a in entry.findall(f"{_ARXIV_NS}author")
            ]
            link = ""
            for l in entry.findall(f"{_ARXIV_NS}link"):
                if l.get("type") in (None, "text/html"):
                    link = l.get("href", "")
            published = entry.findtext(f"{_ARXIV_NS}published") or ""
            year = int(published[:4]) if re.match(r"^\d{4}", published) else None
            if title:
                out.append(RawSource(kind="arxiv", title=title, authors=authors,
                                     year=year, url=link, abstract=summary))
        return out


# ---------------------------------------------------------------------------
# Bundled seed corpus — factual summaries of canonical works. Labeled clearly;
# used when offline or when RLAB_OFFLINE_CORPUS=1.
# ---------------------------------------------------------------------------
SEED_CORPUS: list[RawSource] = [
    RawSource(
        kind="seed_corpus",
        title="Finite-time Analysis of the Multiarmed Bandit Problem",
        authors=["Peter Auer", "Nicolo Cesa-Bianchi", "Paul Fischer"],
        year=2002,
        url="https://doi.org/10.1023/A:1013689704352",
        abstract=(
            "Introduces UCB1, an index policy for stochastic multi-armed bandits "
            "that achieves logarithmic expected regret uniformly over time without "
            "requiring prior knowledge of reward distributions. The analysis shows "
            "regret grows at most O(log T) and establishes distribution-free bounds."
        ),
    ),
    RawSource(
        kind="seed_corpus",
        title="Empirical rankings of bandit algorithms at practical horizons (compiled survey)",
        authors=["RLAB seed-corpus editors"],
        year=2016,
        url=None,
        abstract=(
            "Survey-style discussion noting that empirically observed rankings "
            "between UCB variants and posterior-sampling policies often differ from "
            "asymptotic theory at practical horizons; motivates controlled Monte "
            "Carlo comparisons across gap sizes and horizons."
        ),
    ),
    RawSource(
        kind="seed_corpus",
        title="A Tutorial on Thompson Sampling",
        authors=["Daniel J. Russo", "Benjamin Van Roy", "Abbas Kazerouni", "Ian Osband", "Zheng Wen"],
        year=2018,
        url="https://arxiv.org/abs/1707.02038",
        abstract=(
            "Tutorial covering Thompson sampling for bandits and reinforcement "
            "learning. Emphasizes strong empirical performance of posterior "
            "sampling relative to UCB-type algorithms and discusses intuition for "
            "its adaptive exploration behavior."
        ),
    ),
    RawSource(
        kind="seed_corpus",
        title="Comparison-based parameter tuning heuristics: simulated annealing schedules",
        authors=["Compiled entry summarizing SA schedule literature"],
        year=2004,
        url=None,
        abstract=(
            "Geometric cooling schedules remain standard practice for simulated "
            "annealing; empirical studies report sensitivity to initial temperature "
            "and cooling rate, with too-fast freezing trapping the chain in local "
            "optima on rugged landscapes."
        ),
    ),
    RawSource(
        kind="seed_corpus",
        title="Differential Evolution – A Simple and Efficient Heuristic for Global Optimization over Continuous Spaces",
        authors=["Rainer Storn", "Kenneth Price"],
        year=1997,
        url="https://doi.org/10.1023/A:1008202821328",
        abstract=(
            "Presents differential evolution, a population-based stochastic direct "
            "search method using vector differences for perturbation. Reported to be "
            "effective on multimodal continuous benchmarks with few control "
            "parameters (population size, F, CR)."
        ),
    ),
    RawSource(
        kind="seed_corpus",
        title="No Free Lunch Theorems for Optimization",
        authors=["David H. Wolpert", "William G. Macready"],
        year=1997,
        url="https://doi.org/10.1109/4105.585893",
        abstract=(
            "Formalizes that averaged over all possible problems, no optimization "
            "algorithm outperforms any other. Consequently, claims of solver "
            "superiority must be scoped to specific problem classes — a central "
            "consideration when designing benchmark experiments."
        ),
    ),
    RawSource(
        kind="seed_corpus",
        title="Optimization by Simulated Annealing",
        authors=["Scott Kirkpatrick", "C. Daniel Gelatt", "Mario P. Vecchi"],
        year=1983,
        url="https://doi.org/10.1126/science.220.4598.671",
        abstract=(
            "Introduces simulated annealing: accepting worsening moves with a "
            "temperature-controlled probability enables escape from local optima; "
            "cooling gradually reduces stochasticity toward convergence."
        ),
    ),
    RawSource(
        kind="seed_corpus",
        title="Reducing Monte Carlo Computations: Common Random Numbers and CRN variance reduction",
        authors=["Compiled methodology entry"],
        year=2010,
        url=None,
        abstract=(
            "Common random numbers (CRN) synchronize randomness across compared "
            "configurations so that paired differences have reduced variance, "
            "increasing statistical power of Monte Carlo comparisons without "
            "additional simulation budget."
        ),
    ),
    RawSource(
        kind="seed_corpus",
        title="The Reinforcement Learning problem: exploration vs exploitation",
        authors=["Richard S. Sutton", "Andrew G. Barto"],
        year=2018,
        url="https://mitpress.mit.edu/9780262039246/reinforcement-learning/",
        abstract=(
            "Canonical treatment of bandit problems as the simplest setting "
            "exhibiting the exploration-exploitation dilemma; documents epsilon-"
            "greedy behavior, optimistic initialization, and upper-confidence-bound "
            "methods with regret intuitions."
        ),
    ),
    RawSource(
        kind="seed_corpus",
        title="Empirical comparison of derivative-free optimizers under evaluation budgets",
        authors=["Compiled entry summarizing BBOB practice"],
        year=2012,
        url=None,
        abstract=(
            "Benchmarking practice for black-box optimizers stresses fixed "
            "evaluation budgets, per-problem success thresholds, and reporting "
            "budget-normalized regret curves rather than single-point outcomes; "
            "rankings frequently invert across dimensions and budgets."
        ),
    ),
]


class SeedCorpusProvider:
    kind = "seed_corpus"

    def __init__(self, entries: list[RawSource] | None = None):
        self.entries = entries or SEED_CORPUS

    def search(self, query: str, max_results: int = 10) -> list[RawSource]:
        tokens = _tokenize(query)
        scored = []
        for entry in self.entries:
            text = " ".join([entry.title, entry.abstract]).lower()
            score = sum(1 for t in tokens if t in text)
            scored.append((score, entry))
        scored.sort(key=lambda pair: -pair[0])
        return [e for _, e in scored[:max_results]]


def _tokenize(text: str) -> list[str]:
    from .analysis import _tokenize as _tok
    return _tok(text)
