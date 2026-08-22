# Troubleshooting

## Sessions

**"no proposal possible" / session stops after an error**
All hypothesis strategies were exhausted or errored. Inspect events:
`sqlite3 runs/<sid>/lab.db "SELECT type,payload_json FROM events ORDER BY seq DESC LIMIT 20"`.
Usually every combo/config has been tested — raise `--iterations` won't help;
add knob values or tasks to the domain plugin.

**Experiments all FAIL with empty stderr**
Check `runs/<sid>/expNNN/<label>/stdout.log` — kernels report structured
errors there via result.json; the DB `runs.error` column carries the message.

**Timeouts (status=timeout)**
Kernels must finish well under `RLAB_EXPERIMENT_TIMEOUT_S` (default 120 s).
Large horizons (T=50000+) or DE populations need more time — raise the env
var or lower the task size.

**Reproducibility check fails (IRREPRODUCIBLE blocker)**
A sampled rerun produced different bytes. Causes we've seen:
nondeterministic kernel code (dict iteration over unsorted keys, wall-clock
logic), or BLAS threading differences — the executor pins OMP/MKL threads to 1
and PYTHONHASHSEED=0; kernels must not override these.

## Sandbox

**macOS memory rlimit note:** `RLIMIT_AS` is not reliably enforced on macOS;
the local executor enforces CPU-time and file-size limits plus wall-clock
kill. Use `--executor docker` when hard memory/network isolation matters.

**DockerExecutor: image not present**
`docker build -t rlab-sandbox:latest -f deploy/Dockerfile.sandbox .`

## Server

**Port already in use** → `--port 8621`.

**SSE shows "reconnecting…"** — the stream is long-lived; some proxies buffer
SSE. On localhost this doesn't apply; polling refresh (5 s) still updates the
page regardless.

## Literature

**arXiv returns nothing / errors**
Network egress or rate limiting. The lab automatically falls back to the
bundled seed corpus and marks `online=false`. Force offline mode with
`--offline-corpus` or `RLAB_OFFLINE_CORPUS=1`.

## Common env vars

| Var | Effect |
|---|---|
| `RLAB_ROOT` | workspace dir (default `./runs`) |
| `RLAB_LOG_FORMAT` | `json` (default) or `text` |
| `RLAB_LOG_LEVEL` | DEBUG/INFO/... |
| `RLAB_EXPERIMENT_TIMEOUT_S` | per-run wall clock |
| `RLAB_MAX_PARALLEL_WORKERS` | worker pool size |
