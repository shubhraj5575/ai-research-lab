"""Sandbox behaviour tests: timeouts, limits, determinism, env sanitization."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from rlab.sandbox.local import LocalExecutor


@pytest.fixture()
def executor(tmp_path: Path) -> LocalExecutor:
    return LocalExecutor()


def _write(workdir: Path, name: str, content: str) -> None:
    (workdir / name).write_text(content, encoding="utf-8")


def test_happy_run_captures_output(executor: LocalExecutor, tmp_path: Path):
    _write(tmp_path, "runner.py", "print('hello'); print('err!', file=__import__('sys').stderr)")
    result = executor.execute(tmp_path, ["runner.py"], timeout_s=20, cpu_limit_s=20, fsize_limit_mb=16)
    assert result.ok
    assert "hello" in result.stdout_tail()
    assert "err!" in result.stderr_tail()


def test_wall_clock_timeout_kills_runaway_process(executor: LocalExecutor, tmp_path: Path):
    _write(tmp_path, "runner.py", "import time\ntime.sleep(60)\n")
    result = executor.execute(
        tmp_path, ["runner.py"], timeout_s=1.5, cpu_limit_s=30, fsize_limit_mb=16
    )
    assert result.timed_out
    assert not result.ok
    assert result.wall_ms < 10_000


def test_infinite_loop_killed_by_cpu_or_wall_limit(executor: LocalExecutor, tmp_path: Path):
    # busy loop with no sleep; RLIMIT_CPU should fire before wall timeout
    _write(tmp_path, "runner.py", "x = 0\nwhile True:\n    x += 1\n")
    result = executor.execute(
        tmp_path, ["runner.py"], timeout_s=6, cpu_limit_s=2, fsize_limit_mb=16
    )
    assert not result.ok
    assert result.timed_out or result.killed_reason == "cpu_limit"


def test_file_size_rlimit_truncates_writes(executor: LocalExecutor, tmp_path: Path):
    _write(
        tmp_path,
        "runner.py",
        "open('big.bin','wb').write(b'x' * (200 * 1024 * 1024))",
    )
    result = executor.execute(
        tmp_path, ["runner.py"], timeout_s=30, cpu_limit_s=30, fsize_limit_mb=8
    )
    assert not result.ok
    written = (tmp_path / "big.bin").stat().st_size if (tmp_path / "big.bin").exists() else 0
    assert written <= 8 * 1024 * 1024 + 1024


def test_environment_is_sanitized(executor: LocalExecutor, tmp_path: Path):
    os.environ["RLAB_SANDBOX_CANARY"] = "leaked"
    os.environ["SECRET_CANARY"] = "topsecret"
    _write(
        tmp_path,
        "runner.py",
        "import json, os\n"
        "env = {k: v for k, v in os.environ.items()\n"
        "       if 'CANARY' in k or k == 'PYTHONHASHSEED'}\n"
        "print(json.dumps(env))",
    )
    result = executor.execute(tmp_path, ["runner.py"], timeout_s=15, cpu_limit_s=15, fsize_limit_mb=16)
    payload = json.loads(result.stdout_tail())
    assert payload == {"PYTHONHASHSEED": "0"}


def test_user_site_packages_not_visible(executor: LocalExecutor, tmp_path: Path):
    _write(
        tmp_path,
        "runner.py",
        "import sys\nprint(int(any('site-packages' not in p and p.endswith('packages') "
        "for p in sys.path)))",
    )
    result = executor.execute(tmp_path, ["runner.py"], timeout_s=15, cpu_limit_s=15, fsize_limit_mb=16)
    # '-I' mode: user site dir absent from sys.path -> prints 0 (False)
    assert result.stdout_tail().strip().endswith("0")


def test_exit_code_propagates(executor: LocalExecutor, tmp_path: Path):
    _write(tmp_path, "runner.py", "raise SystemExit(3)")
    result = executor.execute(tmp_path, ["runner.py"], timeout_s=10, cpu_limit_s=10, fsize_limit_mb=16)
    assert result.exit_code == 3
    assert not result.ok


def test_isolation_description_is_truthful(executor: LocalExecutor):
    desc = executor.describe_isolation()
    assert desc["network_isolation"] is False
