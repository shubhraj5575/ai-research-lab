"""Execution sandbox abstraction.

The lab executes untrusted, machine-generated experiment code. The
:class:`Executor` protocol isolates each run in a separate OS process with
resource limits and a hard timeout.

Honest capability statement
---------------------------
* ``LocalExecutor``: subprocess isolation with CPU-time rlimit, file-size
  rlimit, sanitized environment, process-group kill on timeout. It does NOT
  provide network isolation or syscall filtering.
* ``DockerExecutor`` (opt-in): adds namespace isolation including
  ``--network none``, memory/CPU caps and PID limits, when a Docker daemon is
  available. This is the recommended mode for unattended sessions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExecutionResult:
    exit_code: int | None          # None if killed before exit
    timed_out: bool
    wall_ms: float
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    killed_reason: str = ""        # "" | "timeout" | "spawn_error"

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def tail(self, path: Path | None, max_bytes: int = 4000) -> str:
        if path is None or not path.exists():
            return ""
        try:
            data = path.read_bytes()
        except OSError:
            return ""
        if len(data) <= max_bytes:
            return data.decode("utf-8", errors="replace")
        return f"...[{len(data)} bytes truncated]...\n" + data[-max_bytes:].decode("utf-8", errors="replace")

    def stdout_tail(self, max_bytes: int = 4000) -> str:
        return self.tail(self.stdout_path, max_bytes)

    def stderr_tail(self, max_bytes: int = 4000) -> str:
        return self.tail(self.stderr_path, max_bytes)


class Executor(ABC):
    """Runs ``python runner.py`` inside *workdir* under restrictions."""

    name: str = "abstract"

    @abstractmethod
    def execute(
        self,
        workdir: Path,
        command: list[str],
        timeout_s: float,
        cpu_limit_s: float,
        fsize_limit_mb: float,
    ) -> ExecutionResult:
        ...

    @abstractmethod
    def describe_isolation(self) -> dict:
        """Machine-readable description of the guarantees provided."""
