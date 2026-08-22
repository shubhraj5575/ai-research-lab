"""Provenance helpers: environment snapshots and git commit detection."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


def environment_snapshot() -> dict[str, Any]:
    """Metadata recorded with every experiment for reproducibility."""
    import numpy

    return {
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "numpy": numpy.__version__,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
    }


def current_git_commit(repo_hint: Path | None = None) -> str:
    """Return HEAD sha of the enclosing repo, or 'unknown'."""
    candidates = [Path.cwd(), repo_hint] if repo_hint else [Path.cwd()]
    for base in candidates:
        probe = base
        for _ in range(6):  # walk up a few levels
            if (probe / ".git").exists():
                try:
                    out = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=str(probe), capture_output=True, text=True, timeout=10,
                    )
                    if out.returncode == 0:
                        return out.stdout.strip()
                except (OSError, subprocess.TimeoutExpired):
                    pass
                break
            if probe.parent == probe:
                break
            probe = probe.parent
    return "unknown"


def package_versions() -> dict[str, str]:
    """Versions of rlab-adjacent packages relevant to experiments."""
    versions: dict[str, str] = {}
    for mod_name in ("numpy",):
        try:
            mod = __import__(mod_name)
            versions[mod_name] = mod.__version__
        except Exception:
            versions[mod_name] = "unavailable"
    return versions
