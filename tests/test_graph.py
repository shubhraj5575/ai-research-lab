"""Research graph tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from rlab.agents import ResearchDirector
from rlab.config import LabConfig
from rlab.events import EventBus
from rlab.graph.research_graph import ResearchGraph
from rlab.sandbox.local import LocalExecutor
from rlab.store import Store


@pytest.fixture()
def session_with_data(tmp_path: Path):
    cfg = LabConfig(root=tmp_path, seeds_per_config=4, max_iterations=2,
                    max_parallel_workers=4, bootstrap_iters=200,
                    offline_corpus=True, experiment_timeout_s=45.0)
    store = Store(cfg.db_path)
    bus = EventBus()
    original = ResearchDirector._make_executor
    ResearchDirector._make_executor = lambda self: LocalExecutor()
    try:
        director = ResearchDirector(bus=bus, cfg=cfg, store=store)
        ctx = director.start_session("bandit")
        director.run_session(ctx, max_iterations=2)
    finally:
        ResearchDirector._make_executor = original
    return store, ctx.session_id


def test_graph_builds_and_validates(session_with_data):
    store, sid = session_with_data
    g = ResearchGraph(store, sid)
    problems = g.validate()
    assert problems == [], problems
    kinds = {n["kind"] for n in g.nodes.values()}
    assert {"question", "hypothesis", "experiment"} <= kinds


def test_graph_edge_schema_enforced(session_with_data):
    store, sid = session_with_data
    g = ResearchGraph(store, sid)
    # every edge must satisfy the schema
    from rlab.graph.research_graph import EDGE_SCHEMA

    for e in g.edges:
        pair = (g.nodes[e["src"]]["kind"], g.nodes[e["dst"]]["kind"])
        assert e["kind"] in EDGE_SCHEMA
        assert pair in EDGE_SCHEMA[e["kind"]], (e, pair)


def test_evidence_chain_connects_hypothesis_to_critique(session_with_data):
    store, sid = session_with_data
    g = ResearchGraph(store, sid)
    hyps = [n for n in g.nodes.values() if n["kind"] == "hypothesis"]
    assert hyps
    chain = g.evidence_chain(hyps[0]["number"])
    assert chain["experiments"], "hypothesis must have experiments attached"
    exp_node = chain["experiments"][0]
    children_kinds = {g.nodes[c]["kind"] for c in g.children(exp_node["id"])}
    assert "analysis" in children_kinds or "critique" in children_kinds


def test_graphml_export_is_wellformed(session_with_data):
    store, sid = session_with_data
    g = ResearchGraph(store, sid)
    xml = g.to_graphml()
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)
    assert root.tag.endswith("graphml")
    graphs = root.findall("{http://graphml.graphdrawing.org/xmlns}graph")
    assert len(graphs) == 1
    nodes = graphs[0].findall("{http://graphml.graphdrawing.org/xmlns}node")
    assert len(nodes) == len(g.nodes)


def test_unknown_session_raises(tmp_path):
    store = Store(tmp_path / "x.db")
    with pytest.raises(KeyError):
        ResearchGraph(store, "rs_missing")
