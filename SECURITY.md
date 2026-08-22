# Security Review

Status: reviewed after full implementation. Findings and their dispositions
below. Threat model: (a) experiments are machine-generated code executing on
the operator's machine; (b) the dashboard may be exposed to local users;
(c) remote literature sources are fetched.

## 1. Experiment execution isolation

| Control | LocalExecutor | DockerExecutor (opt-in) |
|---|---|---|
| Process boundary | separate `python -I` process | container namespace |
| Wall-clock kill | SIGKILL to process group | docker stop path |
| CPU limit | RLIMIT_CPU (+2 s hard) | `--cpus` |
| File-size limit | RLIMIT_FSIZE | bind-mount scoped |
| Memory limit | ❌ (not enforceable on macOS; documented) | `--memory 512m` |
| Network isolation | ❌ documented | ✅ `--network none` |
| Env sanitization | minimal PATH, HOME=workdir, PYTHONHASHSEED=0, threads=1 | same + image env |
| PID explosion | n/a (single proc) | `--pids-limit 64` |

Kernels shipped with the lab perform no network I/O and write only inside
their workdir. The residual risk of the local executor is explicit in
README/ARCHITECTURE rather than hidden.

## 2. Injection surfaces
* SQL: all queries parameterized; dynamic SET clauses restricted to hardcoded
  column whitelists (`update_hypothesis`, `update_experiment`). Verified by
  grep + tests.
* Shell: no `shell=True`, no `os.system`, no `eval`/`exec`/`pickle` anywhere
  in `src/`.
* Filesystem: artifact paths derive from sanitized variant labels
  (`[^A-Za-z0-9_.-]` → `_`) and server identifiers validated against a strict
  alphabet before touching SQLite or responding.

## 3. Dashboard exposure
* Binds `127.0.0.1` by default (`RLAB_HOST` to override consciously).
* Read-only endpoints only — no POST/PUT/DELETE routes exist.
* Response caps: events ≤2000 rows, runs preview ≤24, sources in graph ≤40,
  SSE queue bounded (500) with keepalives.

## 4. Secrets handling
* LLM API keys are read from the environment at call time
  (`LabConfig.llm_api_key`); they never enter config snapshots, DB rows, logs,
  artifacts, or papers. `.env` is gitignored; repo contains no credentials
  (verified by pattern scan).

## 5. Resource exhaustion (self-DoS)
* Session budgets: max iterations + wall-clock deadline enforced by director.
* Worker pool bounded by `max_parallel_workers`.
* Bootstrap resampling capped via configuration; per-run output files capped
  by RLIMIT_FSIZE; log tails truncated when read into memory.

## 6. Supply chain
* Runtime dependencies: numpy (core), fastapi+uvicorn+httpx (server extras).
  No transitive heavyweights required for research runs. Pinned >= floors,
  not exact pins, to allow security patch uptake; CI exercises 3.11 & 3.12.

## 7. Data integrity
* Result authenticity: every run's metrics are hashed at ingestion
  (`result_hash`) and re-verified by fresh sandboxed re-execution sampled by
  the critic; mismatches produce a blocker finding that forces verdict REJECT.
* Papers cannot cite numbers that are not present in the store; claims.json
  cross-references experiment IDs (referential test enforces this).
