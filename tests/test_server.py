"""Dashboard API tests using FastAPI TestClient (no real sockets)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rlab.agents import ResearchDirector
from rlab.config import LabConfig
from rlab.events import EventBus
from rlab.sandbox.local import LocalExecutor
from rlab.server.app import create_app
from rlab.store import Store


@pytest.fixture()
def client(tmp_path: Path):
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
        sid = ctx.session_id
    finally:
        ResearchDirector._make_executor = original
    app = create_app(cfg, store, bus)
    return TestClient(app), sid, bus


def test_health(client):
    tc, _, _bus = client
    res = tc.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True


def test_sessions_listing_and_detail(client):
    tc, sid, _bus = client
    res = tc.get("/api/sessions")
    ids = [s["id"] for s in res.json()["sessions"]]
    assert sid in ids
    detail = tc.get(f"/api/sessions/{sid}").json()
    assert detail["session"]["id"] == sid
    assert len(detail["hypotheses"]) >= 1
    assert len(detail["experiments"]) >= 1
    exp = detail["experiments"][0]
    for key in ("task", "status", "n_seeds", "variants", "spec_hash"):
        assert key in exp


def test_experiment_detail_contract(client):
    tc, sid, _bus = client
    detail = tc.get(f"/api/sessions/{sid}").json()
    eid = detail["experiments"][0]["id"]
    e = tc.get(f"/api/experiments/{eid}").json()
    assert e["id"] == eid
    assert "analysis" in e and "critiques" in e and "runs_preview" in e


def test_graph_endpoint_json_and_graphml(client):
    tc, sid, _bus = client
    g = tc.get(f"/api/graph/{sid}").json()
    assert g["nodes"] and g["edges"]
    assert isinstance(g["validation"], list)
    res = tc.get(f"/api/graph/{sid}?format=graphml")
    assert res.headers["content-type"].startswith("application/xml")


def test_events_endpoint_filtered(client):
    tc, sid, _bus = client
    rows = tc.get("/api/events", params={"session": sid}).json()["events"]
    assert rows
    assert all(r["session_id"] == sid for r in rows)


def test_invalid_identifier_rejected(client):
    tc, _, _bus = client
    # path traversal must be rejected by the identifier validator
    res = tc.get("/api/sessions/%2e%2e%2fetc%2fpasswd")
    assert res.status_code in (400, 404)
    res2 = tc.get("/api/sessions/" + "x" * 80)
    assert res2.status_code in (400, 404)


def test_unknown_session_404(client):
    tc, _, _bus = client
    # well-formed but unknown id -> 404
    assert tc.get("/api/sessions/rs_0000000zzz").status_code == 404
    assert tc.get("/api/graph/rs_0000000zzz").status_code == 404
    # malformed id (letters outside the Crockford alphabet) -> 400
    assert tc.get("/api/sessions/rs_missing").status_code == 400


def test_index_served_with_static_assets(client):
    tc, _, _bus = client
    html = tc.get("/").text
    assert "AI RESEARCH LAB" in html.upper()
    assert tc.get("/static/app.js").status_code == 200
    assert tc.get("/static/style.css").status_code == 200


def test_stream_emits_published_events(tmp_path):
    """SSE requires a real socket: TestClient buffers complete responses,
    so an infinite event stream would deadlock there by design."""
    import httpx
    import threading
    import time as _time
    import socket as _socket

    import uvicorn

    cfg = LabConfig(root=tmp_path / "sse", seeds_per_config=4, max_iterations=1,
                    offline_corpus=True)
    store = Store(cfg.db_path)
    bus = EventBus()
    app = create_app(cfg, store, bus)

    sock = _socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="critical"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):  # wait for startup
        try:
            if httpx.get(f"{base}/api/health", timeout=1.0).status_code == 200:
                break
        except Exception:
            _time.sleep(0.1)

    received = {"data": False, "tick": False}

    def reader():
        try:
            with httpx.Client(timeout=httpx.Timeout(8.0)) as hc:
                with hc.stream("GET", f"{base}/api/stream") as resp:
                    buf = ""
                    start = _time.time()
                    for chunk in resp.iter_text():
                        buf += chunk
                        if ": connected" in buf:
                            received["data"] = True
                        # publish after connection so delivery is observable
                        if "connected" in buf and not published["done"]:
                            bus.publish("test.tick", session_id="rs_x", n=1)
                            published["done"] = True
                        if "test.tick" in buf:
                            received["tick"] = True
                            break
                        if _time.time() - start > 6:
                            break
        except Exception:
            pass

    published = {"done": False}
    t = threading.Thread(target=reader, daemon=True)
    t.start()
    t.join(timeout=10)
    server.should_exit = True
    thread.join(timeout=5)
    assert received["data"], "never saw the connected comment"
    assert received["tick"], "published event never arrived on the stream"
