"""Local subprocess sandbox.

Guarantees
----------
* fresh interpreter in isolated mode (``-I``: no user site, env ignored)
* sanitized environment (minimal PATH; HOME pointed at the workdir;
  ``PYTHONHASHSEED`` pinned for cross-process determinism of str hashing)
* CPU-time rlimit and file-size rlimit (POSIX ``setrlimit`` in the child via
  ``preexec_fn``, applied before exec)
* wall-clock timeout with process-group SIGKILL escalation
* stdout/stderr streamed to capped files instead of unbounded memory

Not guaranteed (documented limitation): network isolation and syscall
filtering. Use :class:`rlab.sandbox.docker.DockerExecutor` when a stronger
boundary is required.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .base import ExecutionResult, Executor


def _child_limits(cpu_limit_s: float, fsize_limit_mb: float) -> None:  # pragma: no cover - runs in child
    import resource

    cpu = int(max(1, cpu_limit_s))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 2))
    fsize = int(fsize_limit_mb * 1024 * 1024)
    resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
    try:
        # Avoid runaway file descriptor use inside experiments.
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    except (ValueError, OSError):
        pass


class LocalExecutor(Executor):
    name = "local"

    def __init__(self, python_executable: str | None = None) -> None:
        self.python = python_executable or sys.executable

    def execute(
        self,
        workdir: Path,
        command: list[str],
        timeout_s: float,
        cpu_limit_s: float,
        fsize_limit_mb: float,
    ) -> ExecutionResult:
        started = time.monotonic()
        stdout_path = workdir / "stdout.log"
        stderr_path = workdir / "stderr.log"

        env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(workdir),
            "TMPDIR": str(workdir),
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "OMP_NUM_THREADS": "1",   # keep BLAS single-threaded & deterministic-ish
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "LANG": "C.UTF-8",
        }
        full_command = [self.python, "-I", *command]

        stdout_fh = open(stdout_path, "wb")
        stderr_fh = open(stderr_path, "wb")
        try:
            proc = subprocess.Popen(
                full_command,
                cwd=str(workdir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_fh,
                stderr=stderr_fh,
                start_new_session=True,
                preexec_fn=None if os.name != "posix"
                else lambda: _child_limits(cpu_limit_s, fsize_limit_mb),
            )
        except OSError as exc:
            stdout_fh.close()
            stderr_fh.close()
            return ExecutionResult(
                exit_code=None, timed_out=False, wall_ms=0.0,
                stdout_path=stdout_path, stderr_path=stderr_path,
                killed_reason=f"spawn_error: {exc}",
            )

        timed_out = False
        timer_fired = threading.Event()

        def _kill_group() -> None:
            nonlocal timed_out
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            timer_fired.set()

        timer = threading.Timer(timeout_s, _kill_group)
        timer.daemon = True
        timer.start()
        exit_code: int | None = None
        try:
            exit_code = proc.wait()
        finally:
            timer.cancel()
            stdout_fh.close()
            stderr_fh.close()

        wall_ms = (time.monotonic() - started) * 1000.0
        if timed_out:
            # ensure the group is gone even if wait returned spuriously
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return ExecutionResult(
                exit_code=None, timed_out=True, wall_ms=wall_ms,
                stdout_path=stdout_path, stderr_path=stderr_path,
                killed_reason="timeout",
            )
        if exit_code is not None and abs(exit_code) == int(signal.SIGXFSZ):
            killed_reason = "file_size_limit"
        elif exit_code is not None and abs(exit_code) == int(signal.SIGXCPU):
            killed_reason = "cpu_limit"
        else:
            killed_reason = ""
        return ExecutionResult(
            exit_code=exit_code, timed_out=False, wall_ms=wall_ms,
            stdout_path=stdout_path, stderr_path=stderr_path,
            killed_reason=killed_reason,
        )

    def describe_isolation(self) -> dict:
        return {
            "executor": self.name,
            "interpreter": [self.python, "-I"],
            "env": "sanitized (PATH/HOME/TMPDIR pinned, PYTHONHASHSEED=0)",
            "limits": ["RLIMIT_CPU", "RLIMIT_FSIZE", "RLIMIT_NOFILE", "wall-clock killpg"],
            "network_isolation": False,
            "notes": "Subprocess-level isolation. No seccomp/namespace filtering.",
        }
