"""Research graph: typed provenance DAG of the whole session.

Node kinds: question, gap, source, hypothesis, experiment, analysis, critique.
Edges carry semantics (motivated_by, tested_by, produced, reviewed_by,
supported_by, superseded_by). The graph is rebuilt from the store on demand,
validated for integrity, and exportable to JSON and GraphML for external
tooling; the dashboard renders it as a layered SVG from the JSON form.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from ..store import Store

NODE_KINDS = ("question", "gap", "source", "hypothesis", "experiment",
              "analysis", "critique")

# edge type -> allowed (src_kind, dst_kind)
EDGE_SCHEMA = {
    "asks": {("question", "hypothesis")},
    "raises": {("question", "gap")},
    "motivated_by": {("gap", "hypothesis"), ("experiment", "hypothesis"),
                     ("source", "hypothesis")},
    "tested_by": {("hypothesis", "experiment")},
    "produced": {("experiment", "analysis")},
    "reviewed_by": {("experiment", "critique")},
    "cites": {("question", "source"), ("analysis", "source")},
}


class ResearchGraph:
    def __init__(self, store: Store, session_id: str):
        self.store = store
        self.session_id = session_id
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, str]] = []
        self._edge_index: set[tuple[str, str, str]] = set()
        self._build()

    # ------------------------------------------------------------------
    def _add_node(self, node_id: str, kind: str, label: str, **attrs) -> None:
        if kind not in NODE_KINDS:
            raise ValueError(f"unknown node kind {kind!r}")
        self.nodes[node_id] = {"id": node_id, "kind": kind, "label": label} | attrs

    def _add_edge(self, src: str, dst: str, kind: str) -> None:
        key = (src, dst, kind)
        if key in self._edge_index:
            return
        if src not in self.nodes or dst not in self.nodes:
            raise ValueError(f"edge references missing node: {key}")
        allowed = EDGE_SCHEMA.get(kind, set())
        if allowed and (self.nodes[src]["kind"], self.nodes[dst]["kind"]) not in allowed:
            raise ValueError(
                f"invalid edge {kind}: {self.nodes[src]['kind']}->{self.nodes[dst]['kind']}"
            )
        self._edge_index.add(key)
        self.edges.append({"src": src, "dst": dst, "kind": kind})

    # ------------------------------------------------------------------
    def _build(self) -> None:
        session = self.store.get_session(self.session_id)
        if session is None:
            raise KeyError(f"session {self.session_id!r} not found")
        qid = "q:root"
        self._add_node(qid, "question", session["question"],
                       domain=session["domain"], status=session["status"])

        sources = self.store.list_sources(self.session_id)
        for s in sources[:40]:  # keep graphs readable
            sid = f"sr:{s.id}"
            self._add_node(sid, "source", s.title[:70], url=s.url,
                           relevance=s.relevance)
            self._add_edge(qid, sid, "cites")

        gaps = self.store.list_gaps(self.session_id)
        for g in gaps:
            gid = f"gp:{g.id}"
            self._add_node(gid, "gap", g.description[:80], score=g.score)
            self._add_edge(qid, gid, "raises")

        hyps = self.store.list_hypotheses(self.session_id)
        for h in hyps:
            hid = f"hy:{h.id}"
            self._add_node(hid, "hypothesis", f"H{h.number}: {h.claim[:90]}",
                           status=str(h.status), number=h.number,
                           predicted=h.predicted_variant)
            self._add_edge(qid, hid, "asks")
            if h.parent_experiment_id and \
                    f"ex:{h.parent_experiment_id}" in self.nodes:
                self._add_edge(f"ex:{h.parent_experiment_id}", hid,
                               "motivated_by")
            elif h.origin.value == "literature_gap":
                pass

        exps = self.store.list_experiments(self.session_id)
        for e in exps:
            eid = f"ex:{e.id}"
            self._add_node(eid, "experiment",
                           f"E{e.iteration}: {e.config.task}/{e.config.budget_label}",
                           status=str(e.status), spec_hash=e.spec_hash[:12],
                           n_variants=len(e.config.variants),
                           n_seeds=e.config.n_seeds)
            hid = f"hy:{e.hypothesis_id}"
            if hid in self.nodes:
                self._add_edge(hid, eid, "tested_by")
            analysis = self.store.get_analysis(e.id)
            if analysis is not None:
                aid = f"an:{analysis.id}"
                self._add_node(aid, "analysis",
                               f"best={analysis.best_variant}",
                               best=analysis.best_variant,
                               metric=analysis.primary_metric)
                self._add_edge(eid, aid, "produced")
            critiques = self.store.get_critiques(e.id)
            for c in critiques:
                cid = f"cr:{c.id}"
                self._add_node(cid, "critique", f"{str(c.verdict).upper()}",
                               verdict=str(c.verdict))
                self._add_edge(eid, cid, "reviewed_by")

    # ------------------------------------------------------------------
    def validate(self) -> list[str]:
        problems = []
        adjacency = defaultdict(list)
        for e in self.edges:
            adjacency[e["src"]].append(e["dst"])
        # reachability: every node reachable from the question root
        seen = set()
        queue = deque(["q:root"])
        while queue:
            cur = queue.popleft()
            if cur in seen:
                continue
            seen.add(cur)
            queue.extend(adjacency[cur])
        for nid in self.nodes:
            if nid not in seen:
                problems.append(f"unreachable node: {nid}")
        # duplicate edges
        if len(self._edge_index) != len(self.edges):
            problems.append("duplicate edges present")
        return problems

    def children(self, node_id: str) -> list[str]:
        return [e["dst"] for e in self.edges if e["src"] == node_id]

    def parents(self, node_id: str) -> list[str]:
        return [e["src"] for e in self.edges if e["dst"] == node_id]

    def evidence_chain(self, hypothesis_number: int) -> dict[str, Any]:
        """Full provenance chain for one hypothesis."""
        hyp = next((n for n in self.nodes.values()
                    if n["kind"] == "hypothesis" and n.get("number") == hypothesis_number),
                   None)
        if hyp is None:
            return {}
        chain = {"hypothesis": hyp, "experiments": [], "analyses": [],
                 "critiques": []}
        for eid in self.children(hyp["id"]):
            exp_node = self.nodes[eid]
            chain["experiments"].append(exp_node)
            for child in self.children(eid):
                node = self.nodes[child]
                if node["kind"] == "analysis":
                    chain["analyses"].append(node)
                else:
                    chain["critiques"].append(node)
        return chain

    # ------------------------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "nodes": list(self.nodes.values()),
            "edges": self.edges,
        }

    def to_graphml(self) -> str:
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
            '  <key id="kind" for="node" attr.name="kind" attr.type="string"/>',
            '  <key id="label" for="node" attr.name="label" attr.type="string"/>',
            '  <key id="status" for="node" attr.name="status" attr.type="string"/>',
            '  <key id="etype" for="edge" attr.name="type" attr.type="string"/>',
            '  <graph edgedefault="directed">',
        ]
        for n in self.nodes.values():
            status = str(n.get("status", ""))
            label = (n["label"] or "").replace("&", "&amp;").replace("<", "&lt;")
            lines.append(
                f'    <node id="{n["id"]}">'
                f'<data key="kind">{n["kind"]}</data>'
                f'<data key="label">{label}</data>'
                f'<data key="status">{status}</data></node>'
            )
        for i, e in enumerate(self.edges):
            lines.append(
                f'    <edge id="e{i}" source="{e["src"]}" target="{e["dst"]}">'
                f'<data key="etype">{e["kind"]}</data></edge>'
            )
        lines.append("  </graph>")
        lines.append("</graphml>")
        return "\n".join(lines)
