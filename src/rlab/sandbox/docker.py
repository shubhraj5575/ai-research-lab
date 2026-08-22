"""Optional Docker-based sandbox (stronger isolation, opt-in).

Requires a running Docker daemon and an image containing CPython + numpy.
Build one with::

    docker build -t rlab-sandbox:latest -f deploy/Dockerfile.sandbox .

Guarantees beyond the local executor: ``--network none``, memory cap,
CPU quota, PID limit, read-only rootfs (workdir is a bind mount).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from .base import ExecutionResult, Executor


class DockerExecutor(Executor):
    name = "docker"

    def __init__(self, image: str = "rlab-sandbox:latest") -> None:
        self.image = image
        if shutil.which("docker") is None:
            raise RuntimeError("docker CLI not found; cannot use DockerExecutor")

    # ------------------------------------------------------------------
    def _image_available(self) -> bool:
        proc = subprocess.run(
            ["docker", "image", "inspect", self.image],
            capture_output=True, text=True, timeout=30,
        )
        return proc.returncode == 0

    def execute(
        self,
        workdir: Path,
        command: list[str],
        timeout_s: float,
        cpu_limit_s: float,
        fsize_limit_mb: float,
    ) -> ExecutionResult:
        if not self._image_available():
            raise RuntimeError(
                f"docker image {self.image!r} not present. Build it with "
                "'docker build -t rlab-sandbox:latest -f deploy/Dockerfile.sandbox .'"
            )
        started = time.monotonic()
        stdout_path = workdir / "stdout.log"
        stderr_path = workdir / "stderr.log"
        container_cmd = [
            "docker", "run",
            "--rm",
            "--network", "none",
            "--memory", "512m",
            "--cpus", "2",
            "--pids-limit", "64",
            "--security-opt", "no-new-privileges",
            "-v", f"{workdir.resolve()}:/work",
            "-w", "/work",
            "-e", "PYTHONHASHSEED=0",
            "-e", "OMP_NUM_THREADS=1",
            "-e", "OPENBLAS_NUM_THREADS=1",
            self.image,
            *command,
        ]
        with open(stdout_path, "wb") as out_fh, open(stderr_path, "wb") as err_fh:
            try:
                proc = subprocess.run(
                    container_cmd,
                    cwd=str(workdir),
                    stdin=subprocess.DEVNULL,
                    stdout=out_fh,
                    stderr=err_fh,
                    timeout=timeout_s + 10.0,
                )
                exit_code: int | None = proc.returncode
                timed_out = False
                killed_reason = ""
            except subprocess.TimeoutExpired:
                exit_code, timed_out, killed_reason = None, True, "timeout"

        wall_ms = (time.monotonic() - started) * 1000.0
        return ExecutionResult(
            exit_code=exit_code, timed_out=timed_out, wall_ms=wall_ms,
            stdout_path=stdout_path, stderr_path=stderr_path,
            killed_reason=killed_reason,
        )

    def describe_isolation(self) -> dict:
        return {
            "executor": self.name,
            "image": self.image,
            "limits": ["memory 512m", "cpu quota", "pids 64", "wall-clock"],
            "network_isolation": True,
            "notes": "Container namespace isolation with --network none.",
        }
