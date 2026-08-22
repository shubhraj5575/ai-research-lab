"""In-process event bus with a bounded replay buffer.

The bus powers: the audit trail (persistence hook), live dashboard updates
(SSE), and structured logging of lifecycle transitions. Handlers run
synchronously; handler exceptions are isolated and logged, never propagated.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class Event:
    seq: int
    ts: float
    type: str            # e.g. "hypothesis.proposed", "experiment.completed"
    session_id: str | None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type,
            "session_id": self.session_id,
            "payload": self.payload,
        }


Handler = Callable[[Event], None]


class EventBus:
    def __init__(self, replay_size: int = 2000) -> None:
        self._lock = threading.Lock()
        self._handlers: list[Handler] = []
        self._replay: deque[Event] = deque(maxlen=replay_size)
        self._seq = itertools.count(1)
        self.on_error: Callable[[Exception], None] | None = None

    def subscribe(self, handler: Handler) -> None:
        with self._lock:
            self._handlers.append(handler)

    def unsubscribe(self, handler: Handler) -> None:
        with self._lock:
            if handler in self._handlers:
                self._handlers.remove(handler)

    def publish(self, type_: str, session_id: str | None = None, **payload: Any) -> Event:
        event = Event(seq=next(self._seq), ts=time.time(), type=type_,
                      session_id=session_id, payload=payload)
        with self._lock:
            handlers = list(self._handlers)
            self._replay.append(event)
        for h in handlers:
            try:
                h(event)
            except Exception as exc:  # isolate subscriber bugs
                if self.on_error is not None:
                    try:
                        self.on_error(exc)
                    except Exception:
                        pass
        return event

    def recent(self, n: int | None = None) -> list[Event]:
        items = list(self._replay)
        return items[-n:] if n else items

    def clear_handlers(self) -> None:
        with self._lock:
            self._handlers.clear()
