"""Dashboard API server (read-only).

Security posture:
* binds to loopback by default (``RLAB_HOST`` overrides);
* every endpoint is read-only — the dashboard cannot mutate research state;
* identifiers are validated against a strict pattern before hitting SQLite;
* responses are size-capped (events, sources) to bound memory.
"""

from __future__ import annotations

import asyncio
import json
import queue
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..config import LabConfig
from ..events import EventBus
from ..graph.research_graph import ResearchGraph
from ..ids import short_hash
from ..store import Store

_ID_OK = set("0123456789abcdefghjkmnpqrstvwxyz_-.:")


def _check_id(value: str) -> str:
    if not value or len(value) > 64 or not set(value) <= _ID_OK:
        raise HTTPException(status_code=400, detail="invalid identifier")
    return value


def create_app(cfg: LabConfig, store: Store, bus: EventBus | None = None) -> FastAPI:
    app = FastAPI(title="AI Research Lab", version="0.1.0", docs_url="/api/docs")
    started_at = time.time()
    static_dir = Path(__file__).parent / "static"

    # ------------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return {
            "ok": True,
            "uptime_s": round(time.time() - started_at, 1),
            "db": store.path.name,
            "executor": cfg.executor,
        }

    @app.get("/api/sessions")
    def sessions():
        out = []
        for s in store.list_sessions():
            out.append({
                "id": s["id"], "title": s["title"], "domain": s["domain"],
                "status": s["status"], "created_at": s["created_at"],
                "question": s["question"],
            })
        return {"sessions": out}

    @app.get("/api/sessions/{sid}")
    def session_detail(sid: str):
        _check_id(sid)
        s = store.get_session(sid)
        if s is None:
            raise HTTPException(404, "session not found")
        hyps = [h.to_dict() for h in store.list_hypotheses(sid)]
        exps = []
        for e in store.list_experiments(sid):
            analysis = store.get_analysis(e.id)
            crits = store.get_critiques(e.id)
            exps.append({
                "id": e.id,
                "iteration": e.iteration,
                "task": e.config.task,
                "budget_label": e.config.budget_label,
                "n_seeds": e.config.n_seeds,
                "variants": sorted(e.config.variants.keys()),
                "status": str(e.status),
                "wall_ms": e.wall_ms,
                "error": e.error[:300],
                "best_variant": analysis.best_variant if analysis else None,
                "ranking": analysis.ranking if analysis else [],
                "primary_metric": analysis.primary_metric if analysis else None,
                "critique": ({
                    "verdict": c.verdict.value,
                    "findings": [f.code for f in c.issues],
                    "repro_passed": c.repro_check_passed,
                } for c in crits).__next__() if crits else None,
                "spec_hash": e.spec_hash[:12],
            })
        gaps = [g.__dict__ | {"created_at": None} for g in store.list_gaps(sid)]
        return {
            "session": {k: v for k, v in s.items() if k != "config"},
            "config": s["config"],
            "hypotheses": hyps,
            "experiments": exps,
            "gaps": gaps[:8],
        }

    @app.get("/api/experiments/{eid}")
    def experiment_detail(eid: str):
        _check_id(eid)
        e = store.get_experiment(eid)
        if e is None:
            raise HTTPException(404, "experiment not found")
        analysis = store.get_analysis(eid)
        runs = store.list_runs(eid)
        series_by_variant: dict[str, list[list[float]]] = {}
        step = int(runs[0].series.get("curve_step", 1)) if runs and runs[0].series else 1
        for r in runs:
            curve = r.series.get("cumulative_regret") or r.series.get("best_so_far")
            if curve:
                series_by_variant.setdefault(r.variant, []).append(curve)
        avg_series = {}
        for variant, curves in series_by_variant.items():
            n = min(len(c) for c in curves)
            avg_series[variant] = [
                round(sum(c[i] for c in curves) / len(curves), 4) for i in range(n)
            ]
        return {
            "id": e.id,
            "iteration": e.iteration,
            "config": e.config.to_dict(),
            "status": str(e.status),
            "error": e.error,
            "env_json": e.env_json,
            "dataset_ref": e.dataset_ref,
            "git_commit": e.git_commit,
            "code_version": e.code_version,
            "spec_hash": e.spec_hash,
            "analysis": analysis.to_dict() if analysis else None,
            "critiques": [c.to_dict() for c in store.get_critiques(eid)],
            "runs_preview": [
                {"variant": r.variant, "seed": r.seed, "status": r.status,
                 "metrics": r.metrics}
                for r in runs[:24]
            ],
            "runs_total": len(runs),
            "series": {"step": step, "means": avg_series},
        }

    @app.get("/api/graph/{sid}")
    def graph(sid: str, format: str = Query("json", pattern="^(json|graphml)$")):
        _check_id(sid)
        try:
            g = ResearchGraph(store, sid)
        except KeyError:
            raise HTTPException(404, "session not found")
        if format == "graphml":
            from fastapi.responses import Response
            return Response(content=g.to_graphml(), media_type="application/xml")
        data = g.to_json()
        problems = g.validate()
        data["validation"] = problems
        return data

    @app.get("/api/events")
    def events(session: str | None = None, limit: int = Query(200, le=2000)):
        rows = store.list_events(session, limit=limit)
        return {"events": rows}

    # ------------------------------------------------------------------
    @app.get("/api/stream")
    async def stream(session: str | None = None):
        """Server-Sent Events feed of live bus traffic."""
        q: queue.Queue = queue.Queue(maxsize=500)

        def _on_event(event):
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

        bus.subscribe(_on_event)

        async def gen():
            try:
                yield ": connected\n\n"
                loop = asyncio.get_running_loop()
                while True:
                    try:
                        event = await loop.run_in_executor(None, q.get, True, 15)
                        if session and event.session_id != session:
                            continue
                        payload = json.dumps(event.to_dict())
                        yield f"id: {event.seq}\ndata: {payload}\n\n"
                    except queue.Empty:
                        yield ": keepalive\n\n"
            finally:
                bus.unsubscribe(_on_event)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (static_dir / "index.html").read_text(encoding="utf-8")

    return app


def run_server(cfg: LabConfig) -> None:
    import uvicorn

    store = Store(cfg.db_path)
    bus = EventBus()
    app = create_app(cfg, store, bus)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning")
