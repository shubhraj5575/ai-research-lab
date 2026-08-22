# Roadmap

Status labels: ✅ done · 🔨 in progress · 🧭 planned

## v0.1 — Autonomous research core ✅
- ✅ 8-agent loop (director/literature/hypothesis/designer/implementation/
  execution/analyst/critic)
- ✅ Two real research domains (bandit, optim) with deterministic kernels
- ✅ Sandboxed execution with rlimits + timeouts (+ opt-in Docker)
- ✅ Reproducibility: spec hashing, seed derivation, result hashes,
  `rlab verify`, critic spot-checks
- ✅ Hand-rolled, calibrated statistics (Welch/paired/MWU/paired bootstrap/
  Holm/effect sizes/power planning)
- ✅ Provenance graph + GraphML export
- ✅ Evidence-grounded paper generation with claims.json traceability
- ✅ Live dashboard (overview/experiments/graph/timeline, SSE)

## v0.2 — Research quality 🔨
- 🔨 Longer-horizon sessions (50+ iterations) with strategy portfolio
  balancing instead of strict ladder priority
- 🧭 Wilcoxon signed-rank as a third paired test for small n
- 🧭 Adaptive sequential stopping (stop an experiment early when bounds are
  decisively separated) to cut compute
- 🧭 Multi-arm bandit *over strategies*: learn which hypothesis strategy
  yields supported/refuted outcomes per domain and re-order dynamically
- 🧭 Cross-session memory: persist champion/rival knowledge between runs

## v0.3 — Domains 🧭
- 🧭 `sorting` domain: empirical runtime scaling-law fitting vs theory
- 🧭 `mcts` domain: exploration-constant sensitivity on synthetic games
- 🧭 Nonstationary bandit tasks (abrupt drift) to test policy robustness
- 🧭 Noisy optimization (Gaussian observation noise on objectives)

## v0.4 — Platform 🧭
- 🧭 Docker executor hardening: seccomp profile, read-only rootfs tests
- 🧭 Remote dashboard mode with token auth for non-loopback access
- 🧭 Parquet export of runs for external analysis
- 🧭 LLM reasoner prompt suite with numeric-fact grounding checks

## Explicitly out of scope by design
- Web-scale literature crawling (rate limits + ToS); the arXiv adapter stays
  courtesy-rate-limited with caching.
- Any fabricated metrics: simulated components must label themselves.
