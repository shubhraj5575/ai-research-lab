"""Central configuration.

Configuration comes from (highest precedence first):
1. explicit arguments to ``LabConfig(...)``
2. environment variables prefixed with ``RLAB_``
3. defaults

Secrets (LLM API keys) are read from the environment at call time only and are
never persisted into snapshots, logs, or artifacts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any


_ENV_PREFIX = "RLAB_"


def _env_overrides() -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in os.environ.items():
        if not key.startswith(_ENV_PREFIX) or len(key) <= len(_ENV_PREFIX):
            continue
        out[key[len(_ENV_PREFIX):].lower()] = value
    return out


@dataclass
class LabConfig:
    # Where everything lives
    root: Path = field(default_factory=lambda: Path(os.environ.get("RLAB_ROOT", "runs")))

    # Research loop budgets
    max_iterations: int = 24          # hypothesis->experiment iterations
    wall_budget_minutes: float = 45.0 # hard wall-clock budget for a session
    seeds_per_config: int = 30        # Monte Carlo repetitions per configuration
    max_parallel_workers: int = 4     # concurrent sandboxed experiments

    # Sandbox limits (per experiment process)
    experiment_timeout_s: float = 120.0
    cpu_limit_s: float = 150.0
    fsize_limit_mb: float = 64.0      # max bytes an experiment may write

    # Executor selection: "local" (default) or "docker" (opt-in)
    executor: str = "local"
    docker_image: str = "rlab-sandbox:latest"

    # Literature
    literature_cache_dir: str = "litcache"
    arxiv_max_results: int = 12
    offline_corpus: bool = False      # force seed corpus even when online

    # Reasoner: "heuristic" (deterministic, default) or "llm" (needs provider+key)
    reasoner: str = "heuristic"
    llm_provider: str = ""            # "" | anthropic | openai
    llm_model: str = ""

    # Statistics
    bootstrap_iters: int = 2000
    alpha: float = 0.05
    min_effect_d: float = 0.2         # below this, differences are 'negligible'

    # Server
    host: str = "127.0.0.1"
    port: int = 8620

    def __post_init__(self) -> None:
        overrides = _env_overrides()
        for f in fields(self):
            name = f.name
            if name in overrides:
                raw = overrides[name]
                cur = getattr(self, name)
                try:
                    if isinstance(cur, bool):
                        setattr(self, name, raw.lower() in ("1", "true", "yes", "on"))
                    elif isinstance(cur, int):
                        setattr(self, name, int(raw))
                    elif isinstance(cur, float):
                        setattr(self, name, float(raw))
                    elif isinstance(cur, Path):
                        setattr(self, name, Path(raw).expanduser())
                    else:
                        setattr(self, name, raw)
                except (TypeError, ValueError):
                    raise ValueError(f"invalid RLAB_{name.upper()}={raw!r} for {name}")

        self.root = Path(self.root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    # -- derived paths ------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.root / "lab.db"

    def artifact_dir(self, session_id: str) -> Path:
        p = self.root / session_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    # -- secrets (never stored) ----------------------------------------------
    def llm_api_key(self) -> str:
        if self.llm_provider == "anthropic":
            return os.environ.get("ANTHROPIC_API_KEY", "")
        if self.llm_provider == "openai":
            return os.environ.get("OPENAI_API_KEY", "")
        return ""

    def snapshot(self) -> dict[str, Any]:
        """Serializable snapshot WITHOUT any secret material."""
        out: dict[str, Any] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            out[f.name] = str(v) if isinstance(v, Path) else v
        return out
