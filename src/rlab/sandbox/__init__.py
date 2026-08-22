from .base import ExecutionResult, Executor
from .local import LocalExecutor


def make_executor(name: str, **kwargs) -> Executor:
    if name == "local":
        # LocalExecutor takes no image kwarg
        kwargs.pop("image", None)
        return LocalExecutor(**kwargs)
    if name == "docker":
        from .docker import DockerExecutor
        return DockerExecutor(image=kwargs.get("image", "rlab-sandbox:latest"))
    raise ValueError(f"unknown executor {name!r}")


__all__ = ["ExecutionResult", "Executor", "LocalExecutor", "make_executor"]
