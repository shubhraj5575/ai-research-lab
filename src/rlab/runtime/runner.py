"""Experiment runtime: materialization, execution, result ingestion.

One *run* = one (variant, seed) repetition executed in its own sandboxed
workdir containing:

    kernel.py          – verbatim domain kernel source (the "implementation")
    run_config.json    – full configuration snapshot for this single run
    stdout.log / stderr.log
    result.json        – structured outcome written by the kernel

The runtime is responsible for determinism of bookkeeping: variant indices
and child seeds derive from the experiment spec, never from wall-clock or
process state.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import LabConfig
from ..domain.base import DomainPlugin
from ..events import EventBus
from ..ids import new_id, short_hash
from ..jsonlog import get_logger
from ..models import Experiment, ExperimentStatus, RunResult
from ..sandbox.base import Executor
from ..store import Store
from .repro import canonical_json, derive_seed, spec_hash

log = get_logger("runtime")

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


def sanitize(name: str) -> str:
    return _UNSAFE_CHARS.sub("_", name)[:60] or "x"


@dataclass(frozen=True)
class RunTask:
    experiment_id: str
    variant_name: str
    variant_index: int
    seed_index: int
    seed: int


def build_run_tasks(exp: Experiment) -> list[RunTask]:
    """Enumerate all (variant, seed) repetitions in stable order."""
    names = sorted(exp.config.variants.keys())
    tasks = []
    for vi, vname in enumerate(names):
        params = exp.config.variants[vname]
        if not isinstance(params, dict):
            raise ValueError(f"variant {vname!r} params must be an object")
        for ri in range(exp.config.n_seeds):
            tasks.append(RunTask(
                experiment_id=exp.id,
                variant_name=vname,
                variant_index=vi,
                seed_index=ri,
                seed=derive_seed(exp.config.seed_root, ri),
            ))
    return tasks


def compute_spec_hash(exp: Experiment) -> str:
    payload = {
        "domain": exp.config.domain,
        "task": exp.config.task,
        "task_params": exp.config.extra.get("task_params", {}),
        "variants": exp.config.variants,
        "baseline": exp.config.baseline,
        "n_seeds": exp.config.n_seeds,
        "seed_root": exp.config.seed_root,
        "budget_label": exp.config.budget_label,
    }
    return spec_hash(payload)


class ExperimentRunner:
    def __init__(self, store: Store, cfg: LabConfig, executor: Executor, bus: EventBus):
        self.store = store
        self.cfg = cfg
        self.executor = executor
        self.bus = bus

    # -- materialization -----------------------------------------------------
    def materialize(self, workdir: Path, plugin: DomainPlugin, task: str,
                    task_params: dict[str, Any], variant_params: dict[str, Any],
                    seed: int) -> Path:
        # The runtime owns this directory entirely: any previous content is
        # removed so re-execution can never observe stale artifacts.
        import shutil

        if workdir.exists():
            shutil.rmtree(workdir)
        workdir.mkdir(parents=True, exist_ok=False)
        run_cfg = {
            "task": task,
            "task_params": task_params,
            "variant_params": variant_params,
            "seed": int(seed),
        }
        (workdir / "kernel.py").write_text(plugin.kernel_source(), encoding="utf-8")
        (workdir / "run_config.json").write_text(
            canonical_json(run_cfg), encoding="utf-8"
        )
        return workdir

    # -- single run ------------------------------------------------------------
    def execute_run(self, plugin: DomainPlugin, session_dir: Path,
                    exp: Experiment, task_params: dict[str, Any],
                    rt: RunTask, variant_params: dict[str, Any]) -> RunResult:
        label = f"{sanitize(rt.variant_name)}_s{rt.seed}"
        workdir = session_dir / f"exp{exp.iteration:03d}" / label
        started = time.time()
        try:
            self.materialize(workdir, plugin, exp.config.task, task_params,
                             variant_params, rt.seed)
        except OSError as exc:
            return RunResult(id=new_id("run"), experiment_id=exp.id,
                             variant=rt.variant_name, seed=rt.seed,
                             metrics={}, status="failed",
                             error=f"materialize failed: {exc}")

        timeout_s = float(self.cfg.experiment_timeout_s)
        result = self.executor.execute(
            workdir=workdir,
            command=["kernel.py"],
            timeout_s=timeout_s,
            cpu_limit_s=float(self.cfg.cpu_limit_s),
            fsize_limit_mb=float(self.cfg.fsize_limit_mb),
        )
        wall_ms = (time.time() - started) * 1000.0

        rr = RunResult(id=new_id("run"), experiment_id=exp.id,
                       variant=rt.variant_name, seed=rt.seed, metrics={},
                       wall_ms=wall_ms)
        # Prefer the kernel's structured error contract over exit codes:
        # kernels write result.json even when they fail internally.
        raw = None
        try:
            raw = json.loads((workdir / "result.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = None

        if raw is not None and raw.get("status") == "ok":
            rr.metrics = raw.get("metrics", {})
            rr.series = raw.get("series", {})
            rr.result_hash = short_hash(canonical_json(
                {"metrics": rr.metrics, "series": rr.series}), n=32)
            rr.status = "ok"
        elif raw is not None and raw.get("status") == "error":
            rr.status = "failed"
            rr.error = str(raw.get("error", "kernel reported error"))
        elif result.timed_out:
            rr.status = "timeout"
            rr.error = f"exceeded {timeout_s:.0f}s wall clock; stderr tail:\n{result.stderr_tail()}"
        else:
            rr.status = "failed"
            rr.error = (f"exit={result.exit_code} reason={result.killed_reason} "
                        f"stderr tail:\n{result.stderr_tail(1500)}")
        return rr

    # -- whole experiment --------------------------------------------------------
    def run_experiment(self, exp: Experiment, plugin: DomainPlugin,
                       progress_cb=None) -> list[RunResult]:
        """Execute every (variant, seed) repetition with a worker pool."""
        session_dir = self.cfg.artifact_dir(exp.session_id)
        task_params = dict(exp.config.extra.get("task_params", {}))
        tasks = build_run_tasks(exp)

        # ensure the experiment row exists (upsert) so status updates apply
        if self.store.get_experiment(exp.id) is None:
            self.store.save_experiment(exp)
        self.store.update_experiment(exp.id, status=ExperimentStatus.RUNNING,
                                     started_at=time.time())
        self.bus.publish("experiment.started", session_id=exp.session_id,
                         experiment_id=exp.id, iteration=exp.iteration,
                         variants=len(exp.config.variants), seeds=exp.config.n_seeds)

        results: list[RunResult] = []
        failures = 0
        done_count = 0
        pool_started = time.time()
        with ThreadPoolExecutor(max_workers=self.cfg.max_parallel_workers) as pool:
            futures = {
                pool.submit(
                    self.execute_run, plugin, session_dir, exp, task_params,
                    rt, exp.config.variants[rt.variant_name],
                ): rt
                for rt in tasks
            }
            for fut in as_completed(futures):
                rt = futures[fut]
                try:
                    rr = fut.result()
                except Exception as exc:  # defensive: never lose the loop
                    log.error("run_task_crashed", extra={"variant": rt.variant_name,
                                                         "error": repr(exc)})
                    rr = RunResult(id=new_id("run"), experiment_id=exp.id,
                                   variant=rt.variant_name, seed=rt.seed,
                                   metrics={}, status="failed", error=repr(exc))
                results.append(rr)
                self.store.save_run(rr)
                if rr.status != "ok":
                    failures += 1
                done_count += 1
                if progress_cb:
                    progress_cb(done_count, len(tasks), rr)
                self.bus.publish("experiment.run_finished",
                                 session_id=exp.session_id,
                                 experiment_id=exp.id,
                                 variant=rr.variant, seed=rr.seed,
                                 status=rr.status, run_id=rr.id)

        wall_ms = (time.time() - pool_started) * 1000.0
        if failures == len(results) and results:
            status = ExperimentStatus.FAILED
            error = f"all {len(results)} runs failed"
        elif any(r.status == "timeout" for r in results) and all(
                r.status != "ok" for r in results):
            status = ExperimentStatus.TIMEOUT
            error = "all runs timed out"
        else:
            status = ExperimentStatus.COMPLETED
            error = ""
        self.store.update_experiment(exp.id, status=status, finished_at=time.time(),
                                     wall_ms=wall_ms, error=error,
                                     artifact_dir=str(session_dir / f"exp{exp.iteration:03d}"))
        self.bus.publish("experiment.completed", session_id=exp.session_id,
                         experiment_id=exp.id, status=str(status),
                         runs_ok=len(results) - failures, runs_failed=failures)
        log.info("experiment_executed", extra={
            "experiment_id": exp.id, "status": str(status),
            "runs": len(results), "failures": failures})
        return sorted(results, key=lambda r: (r.variant, r.seed))

    # -- reproducibility verification -----------------------------------------------
    def verify_reproducibility(self, exp: Experiment, plugin: DomainPlugin,
                               sample_size: int = 2) -> dict[str, Any]:
        """Re-execute sampled runs from scratch and compare result hashes.

        Returns {"checked": n, "passed": k, "details": [...]}.
        """
        existing = {(r.variant, r.seed): r for r in self.store.list_runs(exp.id)}
        ok_runs = [(vn, sd) for (vn, sd), r in existing.items() if r.status == "ok"]
        if not ok_runs:
            return {"checked": 0, "passed": 0, "details": ["no successful runs to verify"]}
        ok_runs.sort()
        step = max(1, len(ok_runs) // max(1, sample_size))
        picks = [ok_runs[i] for i in range(0, len(ok_runs), step)][:sample_size]

        names = sorted(exp.config.variants.keys())
        session_dir = self.cfg.artifact_dir(exp.session_id)
        task_params = dict(exp.config.extra.get("task_params", {}))
        checked = passed = 0
        details = []
        for variant, seed in picks:
            vi = names.index(variant)
            original_seed = None
            # find the original seed index to reproduce the exact derived seed
            for ri in range(exp.config.n_seeds):
                if derive_seed(exp.config.seed_root, ri) == seed:
                    original_seed = (vi, ri)
                    break
            if original_seed is None:
                continue
            vi, ri = original_seed
            rt = RunTask(experiment_id=exp.id + "-verify", variant_name=variant,
                         variant_index=vi, seed_index=ri, seed=seed)
            fresh = self.execute_run(plugin, session_dir / "_verify", exp,
                                     task_params, rt, exp.config.variants[variant])
            ref = existing[(variant, seed)]
            checked += 1
            match = (fresh.status == "ok" and fresh.result_hash != "" and
                     fresh.result_hash == ref.result_hash)
            passed += match
            details.append({
                "variant": variant, "seed": seed, "match": match,
                "ref_hash": ref.result_hash[:12], "new_hash": fresh.result_hash[:12],
            })
        return {"checked": checked, "passed": passed, "details": details}
