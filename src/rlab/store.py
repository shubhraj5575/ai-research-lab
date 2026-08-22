"""SQLite persistence layer.

Design notes
------------
* One connection guarded by an RLock; SQLite in WAL mode for concurrent reads.
* Explicit, versioned migrations (``schema_migrations`` table).
* Typed CRUD over the dataclasses in :mod:`rlab.models`; JSON columns store
  structured payloads so the schema can evolve without losing history.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from .models import (
    Analysis,
    Comparison,
    Critique,
    CritiqueFinding,
    CritiqueVerdict,
    Experiment,
    ExperimentConfig,
    ExperimentStatus,
    Hypothesis,
    HypothesisStatus,
    OriginKind,
    ResearchGap,
    RunResult,
    Source,
)
from .ids import new_id

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    question TEXT NOT NULL,
    domain TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    config_json TEXT NOT NULL,
    git_commit TEXT,
    title TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    authors_json TEXT NOT NULL DEFAULT '[]',
    year INTEGER,
    url TEXT,
    abstract TEXT DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    relevance REAL,
    fetched_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sources_session ON sources(session_id);
CREATE TABLE IF NOT EXISTS gaps (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    description TEXT NOT NULL,
    evidence_source_ids_json TEXT NOT NULL DEFAULT '[]',
    score REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS hypotheses (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    number INTEGER NOT NULL,
    claim TEXT NOT NULL,
    reasoning TEXT NOT NULL DEFAULT '',
    expected_result TEXT NOT NULL DEFAULT '',
    falsification_condition TEXT NOT NULL DEFAULT '',
    required_experiment TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL,
    parent_experiment_id TEXT,
    status TEXT NOT NULL,
    confidence REAL,
    resolution_note TEXT DEFAULT '',
    created_at REAL NOT NULL,
    resolved_at REAL
);
CREATE INDEX IF NOT EXISTS idx_hyp_session ON hypotheses(session_id);
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    spec_hash TEXT NOT NULL,
    code_version TEXT NOT NULL DEFAULT '',
    git_commit TEXT NOT NULL DEFAULT '',
    env_json TEXT NOT NULL DEFAULT '{}',
    dataset_ref_json TEXT NOT NULL DEFAULT '{}',
    config_json TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT DEFAULT '',
    wall_ms REAL NOT NULL DEFAULT 0,
    started_at REAL,
    finished_at REAL,
    artifact_dir TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_exp_session ON experiments(session_id);
CREATE INDEX IF NOT EXISTS idx_exp_hyp ON experiments(hypothesis_id);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    variant TEXT NOT NULL,
    seed INTEGER NOT NULL,
    metrics_json TEXT NOT NULL,
    series_json TEXT NOT NULL DEFAULT '{}',
    wall_ms REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',
    error TEXT DEFAULT '',
    result_hash TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_exp ON runs(experiment_id);
CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL UNIQUE,
    primary_metric TEXT NOT NULL,
    direction TEXT NOT NULL,
    comparisons_json TEXT NOT NULL,
    summary TEXT DEFAULT '',
    best_variant TEXT DEFAULT '',
    ranking_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS critiques (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    issues_json TEXT NOT NULL,
    text TEXT DEFAULT '',
    repro_check_passed INTEGER,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_critique_exp ON critiques(experiment_id);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    session_id TEXT,
    payload_json TEXT NOT NULL
);
"""),
]


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    # -- lifecycle -----------------------------------------------------------
    def migrate(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL)"
            )
            applied = {r["version"] for r in self._conn.execute("SELECT version FROM schema_migrations")}
            for version, sql in MIGRATIONS:
                if version not in applied:
                    self._conn.executescript(sql)
                    self._conn.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (version, time.time()),
                    )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    @staticmethod
    def _j(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)

    # -- sessions -------------------------------------------------------------
    def create_session(self, session_id: str, question: str, domain: str,
                       config_snapshot: dict[str, Any], git_commit: str,
                       title: str = "") -> None:
        self._exec(
            "INSERT INTO sessions(id, created_at, question, domain, status, config_json, git_commit, title)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (session_id, time.time(), question, domain, "running", self._j(config_snapshot), git_commit, title),
        )
        self._conn.commit()

    def set_session_status(self, session_id: str, status: str) -> None:
        self._exec("UPDATE sessions SET status=? WHERE id=?", (status, session_id))
        self._conn.commit()

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        row = self._exec("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) | {"config": json.loads(row["config_json"])} if row else None

    def list_sessions(self) -> list[dict[str, Any]]:
        rows = self._exec("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["config"] = json.loads(d.pop("config_json"))
            out.append(d)
        return out

    # -- sources ---------------------------------------------------------------
    def add_source(self, s: Source) -> None:
        self._exec(
            "INSERT OR REPLACE INTO sources(id, session_id, kind, title, authors_json, year, url,"
            " abstract, tags_json, relevance, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (s.id, s.session_id, s.kind, s.title, self._j(s.authors), s.year, s.url,
             s.abstract, self._j(s.tags), s.relevance, s.fetched_at),
        )
        self._conn.commit()

    def list_sources(self, session_id: str) -> list[Source]:
        rows = self._exec("SELECT * FROM sources WHERE session_id=? ORDER BY fetched_at", (session_id,)).fetchall()
        return [
            Source(
                id=r["id"], session_id=r["session_id"], kind=r["kind"], title=r["title"],
                authors=json.loads(r["authors_json"]), year=r["year"], url=r["url"],
                abstract=r["abstract"], tags=json.loads(r["tags_json"]),
                relevance=r["relevance"], fetched_at=r["fetched_at"],
            ) for r in rows
        ]

    # -- gaps -------------------------------------------------------------------
    def add_gap(self, g: ResearchGap) -> None:
        self._exec(
            "INSERT OR REPLACE INTO gaps(id, session_id, description, evidence_source_ids_json, score, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (g.id, g.session_id, g.description, self._j(g.evidence_source_ids), g.score, g.created_at),
        )
        self._conn.commit()

    def list_gaps(self, session_id: str) -> list[ResearchGap]:
        rows = self._exec("SELECT * FROM gaps WHERE session_id=? ORDER BY score DESC", (session_id,)).fetchall()
        return [ResearchGap(r["id"], r["session_id"], r["description"],
                            json.loads(r["evidence_source_ids_json"]), r["score"], r["created_at"])
                for r in rows]

    # -- hypotheses --------------------------------------------------------------
    def save_hypothesis(self, h: Hypothesis) -> None:
        self._exec(
            "INSERT OR REPLACE INTO hypotheses(id, session_id, number, claim, reasoning, expected_result,"
            " falsification_condition, required_experiment, origin, parent_experiment_id, status,"
            " confidence, resolution_note, created_at, resolved_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (h.id, h.session_id, h.number, h.claim, h.reasoning, h.expected_result,
             h.falsification_condition, h.required_experiment, str(h.origin),
             h.parent_experiment_id, str(h.status), h.confidence, h.resolution_note,
             h.created_at, h.resolved_at),
        )
        self._conn.commit()

    def get_hypothesis(self, hyp_id: str) -> Hypothesis | None:
        row = self._exec("SELECT * FROM hypotheses WHERE id=?", (hyp_id,)).fetchone()
        return self._row_to_hypothesis(row) if row else None

    def update_hypothesis(self, hyp_id: str, **fields: Any) -> None:
        allowed = {"claim", "reasoning", "expected_result", "falsification_condition",
                   "required_experiment", "origin", "parent_experiment_id", "status",
                   "confidence", "resolution_note", "resolved_at"}
        sets, params = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise KeyError(f"cannot update hypothesis field {key}")
            sets.append(f"{key}=?")
            params.append(str(value))
        params.append(hyp_id)
        self._exec(f"UPDATE hypotheses SET {', '.join(sets)} WHERE id=?", params)
        self._conn.commit()

    def list_hypotheses(self, session_id: str) -> list[Hypothesis]:
        rows = self._exec("SELECT * FROM hypotheses WHERE session_id=? ORDER BY number", (session_id,)).fetchall()
        return [self._row_to_hypothesis(r) for r in rows]

    @staticmethod
    def _row_to_hypothesis(row: sqlite3.Row) -> Hypothesis:
        return Hypothesis(
            id=row["id"], session_id=row["session_id"], number=row["number"], claim=row["claim"],
            reasoning=row["reasoning"], expected_result=row["expected_result"],
            falsification_condition=row["falsification_condition"],
            required_experiment=row["required_experiment"], origin=OriginKind(row["origin"]),
            parent_experiment_id=row["parent_experiment_id"],
            status=HypothesisStatus(row["status"]), confidence=row["confidence"],
            resolution_note=row["resolution_note"] or "", created_at=row["created_at"],
            resolved_at=row["resolved_at"],
        )

    # -- experiments ---------------------------------------------------------------
    def save_experiment(self, e: Experiment) -> None:
        self._exec(
            "INSERT OR REPLACE INTO experiments(id, session_id, hypothesis_id, iteration, spec_hash,"
            " code_version, git_commit, env_json, dataset_ref_json, config_json, status, error,"
            " wall_ms, started_at, finished_at, artifact_dir) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (e.id, e.session_id, e.hypothesis_id, e.iteration, e.spec_hash, e.code_version,
             e.git_commit, self._j(e.env_json), self._j(e.dataset_ref), self._j(e.config.to_dict()),
             str(e.status), e.error, e.wall_ms, e.started_at, e.finished_at, e.artifact_dir),
        )
        self._conn.commit()

    def update_experiment(self, exp_id: str, **fields: Any) -> None:
        allowed = {"status", "error", "wall_ms", "started_at", "finished_at", "artifact_dir",
                   "code_version", "git_commit"}
        sets, params = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise KeyError(f"cannot update experiment field {key}")
            sets.append(f"{key}=?")
            if key == "status":
                value = str(value)
            params.append(value)
        params.append(exp_id)
        self._exec(f"UPDATE experiments SET {', '.join(sets)} WHERE id=?", params)
        self._conn.commit()

    def get_experiment(self, exp_id: str) -> Experiment | None:
        row = self._exec("SELECT * FROM experiments WHERE id=?", (exp_id,)).fetchone()
        return self._row_to_experiment(row) if row else None

    def find_experiment_by_spec(self, spec_hash: str) -> Experiment | None:
        row = self._exec(
            "SELECT * FROM experiments WHERE spec_hash=? AND status IN ('completed','timeout') ORDER BY started_at DESC LIMIT 1",
            (spec_hash,),
        ).fetchone()
        return self._row_to_experiment(row) if row else None

    def list_experiments(self, session_id: str) -> list[Experiment]:
        rows = self._exec("SELECT * FROM experiments WHERE session_id=? ORDER BY iteration", (session_id,)).fetchall()
        return [self._row_to_experiment(r) for r in rows]

    @staticmethod
    def _row_to_experiment(row: sqlite3.Row) -> Experiment:
        cfg = json.loads(row["config_json"])
        return Experiment(
            id=row["id"], session_id=row["session_id"], hypothesis_id=row["hypothesis_id"],
            iteration=row["iteration"], config=ExperimentConfig(**cfg), spec_hash=row["spec_hash"],
            code_version=row["code_version"], git_commit=row["git_commit"],
            env_json=json.loads(row["env_json"]), dataset_ref=json.loads(row["dataset_ref_json"]),
            status=ExperimentStatus(row["status"]), error=row["error"] or "",
            wall_ms=row["wall_ms"], started_at=row["started_at"], finished_at=row["finished_at"],
            artifact_dir=row["artifact_dir"] or "",
        )

    # -- runs ----------------------------------------------------------------------
    def save_run(self, r: RunResult) -> None:
        self._exec(
            "INSERT OR REPLACE INTO runs(id, experiment_id, variant, seed, metrics_json, series_json,"
            " wall_ms, status, error, result_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r.id, r.experiment_id, r.variant, r.seed, self._j(r.metrics), self._j(r.series),
             r.wall_ms, r.status, r.error, r.result_hash),
        )
        self._conn.commit()

    def list_runs(self, experiment_id: str) -> list[RunResult]:
        rows = self._exec("SELECT * FROM runs WHERE experiment_id=? ORDER BY variant, seed",
                          (experiment_id,)).fetchall()
        return [RunResult(r["id"], r["experiment_id"], r["variant"], r["seed"],
                          json.loads(r["metrics_json"]), json.loads(r["series_json"]),
                          r["wall_ms"], r["status"], r["error"] or "", r["result_hash"])
                for r in rows]

    def get_runs_by_hash(self, experiment_id: str, variant: str, seed: int) -> RunResult | None:
        row = self._exec("SELECT * FROM runs WHERE experiment_id=? AND variant=? AND seed=?",
                         (experiment_id, variant, seed)).fetchone()
        if not row:
            return None
        return RunResult(row["id"], row["experiment_id"], row["variant"], row["seed"],
                         json.loads(row["metrics_json"]), json.loads(row["series_json"]),
                         row["wall_ms"], row["status"], row["error"] or "", row["result_hash"])

    # -- analyses ---------------------------------------------------------------------
    def save_analysis(self, a: Analysis) -> None:
        self._exec(
            "INSERT OR REPLACE INTO analyses(id, experiment_id, primary_metric, direction,"
            " comparisons_json, summary, best_variant, ranking_json) VALUES (?,?,?,?,?,?,?,?)",
            (a.id, a.experiment_id, a.primary_metric, a.direction, self._j([c.__dict__ for c in a.comparisons]),
             a.summary, a.best_variant, self._j(a.ranking)),
        )
        self._conn.commit()

    def get_analysis(self, experiment_id: str) -> Analysis | None:
        row = self._exec("SELECT * FROM analyses WHERE experiment_id=?", (experiment_id,)).fetchone()
        if not row:
            return None
        comps = [Comparison(**c) for c in json.loads(row["comparisons_json"])]
        ranking = [(v, float(m)) for v, m in json.loads(row["ranking_json"])]
        return Analysis(row["id"], row["experiment_id"], row["primary_metric"], row["direction"],
                        comps, row["summary"], row["best_variant"], ranking)

    # -- critiques ------------------------------------------------------------------------
    def save_critique(self, c: Critique) -> None:
        self._exec(
            "INSERT OR REPLACE INTO critiques(id, experiment_id, hypothesis_id, verdict, issues_json,"
            " text, repro_check_passed, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (c.id, c.experiment_id, c.hypothesis_id, str(c.verdict), self._j([i.to_dict() for i in c.issues]),
             c.text, c.repro_check_passed, c.created_at),
        )
        self._conn.commit()

    def get_critiques(self, experiment_id: str) -> list[Critique]:
        rows = self._exec("SELECT * FROM critiques WHERE experiment_id=? ORDER BY created_at",
                          (experiment_id,)).fetchall()
        out = []
        for r in rows:
            issues = [CritiqueFinding(**i) for i in json.loads(r["issues_json"])]
            repro = None if r["repro_check_passed"] is None else bool(r["repro_check_passed"])
            out.append(Critique(r["id"], r["experiment_id"], r["hypothesis_id"],
                                CritiqueVerdict(r["verdict"]), issues, r["text"], repro, r["created_at"]))
        return out

    # -- events -----------------------------------------------------------------------------
    def persist_event(self, event_dict: dict[str, Any]) -> None:
        self._exec("INSERT INTO events(ts, type, session_id, payload_json) VALUES (?,?,?,?)",
                   (event_dict["ts"], event_dict["type"], event_dict["session_id"],
                    self._j(event_dict["payload"])))
        self._conn.commit()

    def list_events(self, session_id: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        if session_id:
            rows = self._exec("SELECT * FROM events WHERE session_id=? ORDER BY seq DESC LIMIT ?",
                              (session_id, limit)).fetchall()
        else:
            rows = self._exec("SELECT * FROM events ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        return [{"seq": r["seq"], "ts": r["ts"], "type": r["type"], "session_id": r["session_id"],
                 "payload": json.loads(r["payload_json"])} for r in rows]

    # -- aggregate helpers --------------------------------------------------------------------
    def session_summary(self, session_id: str) -> dict[str, Any]:
        hyps = self.list_hypotheses(session_id)
        exps = self.list_experiments(session_id)
        by_status: dict[str, int] = {}
        for e in exps:
            by_status[str(e.status)] = by_status.get(str(e.status), 0) + 1
        return {
            "session_id": session_id,
            "hypotheses_total": len(hyps),
            "hypotheses_supported": sum(1 for h in hyps if h.status == HypothesisStatus.SUPPORTED),
            "hypotheses_refuted": sum(1 for h in hyps if h.status == HypothesisStatus.REFUTED),
            "experiments_total": len(exps),
            "experiments_by_status": by_status,
            "runs_total": sum(1 for e in exps for _ in self.list_runs(e.id)),
        }
