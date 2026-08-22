from .base import ExecutionResult, Executor
from .local import LocalExecutor


def make_executor(name: str, **kwargs) -> Executor:
    if name == "local":
        return LocalExecutor(**kwargs)
    if name == "docker":
        from .docker import DockerExecutor
        return DockerExecutor(**kwargs)
    raise ValueError(f"unknown executor {name!r}")


__all__ = ["ExecutionResult", "Executor", "LocalExecutor", "make_executor"]
