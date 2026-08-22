"""Paper + figure generation tests (fed by a real mini session)."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from rlab.agents import ResearchDirector
from rlab.config import LabConfig
from rlab.events import EventBus
from rlab.reports.figures import comparison_bars, line_chart, outcome_timeline
from rlab.reports.paper import PaperGenerator
from rlab.sandbox.local import LocalExecutor
from rlab.store import Store


@pytest.fixture(scope="module")
def finished_session(tmp_path_factory):
    root = tmp_path_factory.mktemp("paper_lab")
    cfg = LabConfig(root=root, seeds_per_config=6, max_iterations=3,
                    max_parallel_workers=4, bootstrap_iters=300,
                    offline_corpus=True, experiment_timeout_s=45.0)
    store = Store(cfg.db_path)
    bus = EventBus()
    original = ResearchDirector._make_executor
    ResearchDirector._make_executor = lambda self: LocalExecutor()
    try:
        director = ResearchDirector(bus=bus, cfg=cfg, store=store)
        ctx = director.start_session("bandit")
        director.run_session(ctx, max_iterations=3)
    finally:
        ResearchDirector._make_executor = original
    return cfg, store, ctx.session_id


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def test_line_chart_deterministic_and_valid(tmp_path):
    series = {"ucb1": [0, 10, 25, 40, 55], "eps_greedy": [0, 30, 60, 90, 120]}
    p1 = line_chart(series, "t", "step", "regret", tmp_path / "a.svg", x_scale=100)
    p2 = line_chart(series, "t", "step", "regret", tmp_path / "b.svg", x_scale=100)
    assert p1.read_bytes() == p2.read_bytes()
    root = ET.fromstring(p1.read_text())
    assert root.tag.endswith("svg")
    polylines = [e for e in root.iter() if e.tag.endswith("polyline")]
    assert len(polylines) == 2


def test_comparison_bars_valid(tmp_path):
    p = comparison_bars([("a", 10, 8, 12), ("b", 15, 13.5, 16)],
                        "rank", "metric", tmp_path / "bars.svg")
    ET.fromstring(p.read_text())


def test_outcome_timeline_valid(tmp_path):
    p = outcome_timeline([(1, "supported"), (2, "refuted"), (3, "inconclusive")],
                         "outcomes", tmp_path / "tl.svg")
    ET.fromstring(p.read_text())


def test_nice_ticks_sane():
    from rlab.reports.figures import _nice_ticks

    ticks = _nice_ticks(0, 100)
    assert ticks[0] == 0 and ticks[-1] <= 100
    assert all(b > a for a, b in zip(ticks, ticks[1:]))


# ---------------------------------------------------------------------------
# Paper
# ---------------------------------------------------------------------------
def test_paper_contains_required_sections(finished_session, tmp_path):
    cfg, store, sid = finished_session
    gen = PaperGenerator(cfg, store)
    artifacts = gen.generate(sid, tmp_path / "report")
    text = artifacts.markdown_path.read_text()
    for section in ["Abstract", "Introduction", "Related Work",
                    "Methodology", "Experiments and Results",
                    "Limitations", "Conclusion", "Future Work",
                    "Provenance and Traceability"]:
        assert f"## {section}" in text, section
    # evidence discipline: experiment IDs must be cited somewhere
    exps = store.list_experiments(sid)
    assert exps
    assert any(e.id in text for e in exps), "no experiment IDs cited"


def test_claims_json_references_real_experiments(finished_session, tmp_path):
    cfg, store, sid = finished_session
    gen = PaperGenerator(cfg, store)
    artifacts = gen.generate(sid, tmp_path / "report")
    doc = json.loads(artifacts.claims_path.read_text())
    assert doc["session_id"] == sid
    real_ids = {e.id for e in store.list_experiments(sid)}
    for claim in doc["claims"]:
        assert set(claim["evidence"]) <= real_ids, claim


def test_paper_has_no_placeholder_prose(finished_session, tmp_path):
    cfg, store, sid = finished_session
    gen = PaperGenerator(cfg, store)
    artifacts = gen.generate(sid, tmp_path / "report")
    text = artifacts.markdown_path.read_text().lower()
    for bad in ("lorem ipsum", "todo", "placeholder text"):
        assert bad not in text


def test_paper_figures_exist(finished_session, tmp_path):
    cfg, store, sid = finished_session
    gen = PaperGenerator(cfg, store)
    artifacts = gen.generate(sid, tmp_path / "report")
    assert artifacts.figure_files, "expected at least one figure"
    for name in artifacts.figure_files:
        svg = (artifacts.figures_dir / name).read_text()
        ET.fromstring(svg)


def test_unknown_session_raises(finished_session, tmp_path):
    cfg, store, _sid = finished_session
    with pytest.raises(KeyError):
        PaperGenerator(cfg, store).generate("rs_missing", tmp_path / "x")
