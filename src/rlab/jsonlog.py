"""Structured JSON logging.

Every log line is a single JSON object so that runs can be grepped, shipped to
a log aggregator, or replayed. A human renderer is available via
``RLAB_LOG_FORMAT=text``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": round(record.created, 4),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = repr(value)
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int | None = None) -> logging.Logger:
    """Configure the root ``rlab`` logger. Idempotent."""
    logger = logging.getLogger("rlab")
    if getattr(logger, "_rlab_configured", False):
        return logger
    fmt = os.environ.get("RLAB_LOG_FORMAT", "json").lower()
    level = level or getattr(
        logging, os.environ.get("RLAB_LOG_LEVEL", "INFO").upper(), logging.INFO
    )
    handler = logging.StreamHandler(sys.stderr)
    if fmt == "text":
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s %(message)s"))
    else:
        handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    logger._rlab_configured = True  # type: ignore[attr-defined]
    return logger


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"rlab.{name}")
