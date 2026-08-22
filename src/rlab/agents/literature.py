"""Literature Agent: discovery, extraction, comparison, gaps, organization."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import LabConfig
from ..domain.base import DomainPlugin
from ..events import EventBus
from ..ids import new_id
from ..literature.analysis import (
    TfidfSpace,
    compare_sources,
    extract_keyphrases,
    identify_gaps,
    organize_themes,
)
from ..literature.providers import ArxivProvider, SeedCorpusProvider
from ..models import ResearchGap, Source
from ..store import Store
from .base import Agent


@dataclass
class LiteratureBrief:
    sources: list[Source]
    themes: list[dict]
    similar_pairs: list[dict]
    gaps: list[ResearchGap]
    online_used: bool = False

    def to_dict(self) -> dict:
        return {
            "n_sources": len(self.sources),
            "themes": self.themes,
            "similar_pairs": self.similar_pairs,
            "gaps": [g.__dict__ for g in self.gaps],
            "online_used": self.online_used,
        }


class LiteratureAgent(Agent):
    role = "literature"

    def __init__(self, bus: EventBus, cfg: LabConfig, store: Store,
                 arxiv: ArxivProvider | None = None):
        super().__init__(bus)
        self.cfg = cfg
        self.store = store
        self.arxiv = arxiv or ArxivProvider(
            cache=DiskCache(cfg.root / cfg.literature_cache_dir),
        )
        self.seed = SeedCorpusProvider()

    # ------------------------------------------------------------------
    def survey(self, session_id: str, question: str,
               plugin: DomainPlugin) -> LiteratureBrief:
        """Run the full literature pipeline for a research question."""
        self.announce(session_id, "survey_started", question=question)

        raw_sources: list = []
        online_used = False
        if not self.cfg.offline_corpus:
            queries = plugin.literature_queries()
            for query in queries[:2]:  # courtesy-rate-limited; keep small
                found = self.arxiv.search(query, max_results=self.cfg.arxiv_max_results)
                if found and self.arxiv.last_error is None:
                    raw_sources.extend(found)
                    online_used = True
                elif self.arxiv.last_error:
                    self.log.warning("arxiv_unavailable", extra={"error": self.arxiv.last_error})
                    break  # fall back below
        if not raw_sources:
            raw_sources = list(self.seed.entries)

        # dedupe by title (casefolded)
        seen_titles: set[str] = set()
        unique_raw = []
        for s in raw_sources:
            key = " ".join(s.title.lower().split())
            if key not in seen_titles:
                seen_titles.add(key)
                unique_raw.append(s)

        documents = [f"{s.title}. {s.abstract}" for s in unique_raw]
        space = TfidfSpace(documents)
        relevance = space.relevance(question)
        themes = organize_themes(documents, k=min(4, max(2, len(unique_raw) // 3)))
        pairs = compare_sources(space, [s.title for s in unique_raw])
        gap_terms = identify_gaps(question, documents)

        sources: list[Source] = []
        for i, s in enumerate(unique_raw):
            src = Source(
                id=new_id("source"),
                session_id=session_id,
                kind=s.kind,
                title=s.title,
                authors=s.authors,
                year=s.year,
                url=s.url,
                abstract=s.abstract,
                tags=extract_keyphrases(f"{s.title}. {s.abstract}"),
                relevance=round(float(relevance[i]), 4) if relevance.size else None,
            )
            self.store.add_source(src)
            sources.append(src)

        id_by_index = {i: src.id for i, src in enumerate(sources)}
        gaps: list[ResearchGap] = []
        score_base = 1.0
        for g in gap_terms:
            evidence = [id_by_index[i] for i in range(len(sources))][:0]  # gaps are corpus-level
            gap = ResearchGap(
                id=new_id("gap"),
                session_id=session_id,
                description=(
                    f"Literature coverage is thin on '{g['term']}' "
                    f"(question frequency {g['question_frequency']}, corpus coverage "
                    f"{int(g['corpus_coverage'] * 100)}%). Candidate angle for "
                    "experimentation."
                ),
                evidence_source_ids=evidence,
                score=round(score_base * g["question_frequency"] *
                            (1.0 - g["corpus_coverage"]), 3),
            )
            score_base *= 0.7
            self.store.add_gap(gap)
            gaps.append(gap)

        brief = LiteratureBrief(
            sources=sources, themes=themes, similar_pairs=pairs,
            gaps=gaps, online_used=online_used,
        )
        self.store.persist_event({
            "ts": __import__("time").time(),
            "type": "literature.brief",
            "session_id": session_id,
            "payload": {
                "reasoner": "heuristic",
                "sources": len(sources),
                "themes": len(themes),
                "gaps": len(gaps),
                "online": online_used,
            },
        })
        self.announce(session_id, "survey_completed",
                      sources=len(sources), themes=len(themes), gaps=len(gaps),
                      online=online_used)
        return brief
