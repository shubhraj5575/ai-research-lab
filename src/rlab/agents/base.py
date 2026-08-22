"""Agent framework: shared plumbing for the research team."""

from __future__ import annotations

from ..events import EventBus
from ..jsonlog import get_logger


class Agent:
    """Base class providing identity, logging, and event emission."""

    role: str = "agent"

    def __init__(self, bus: EventBus):
        self.bus = bus
        self.log = get_logger(f"agent.{self.role}")

    def announce(self, session_id: str, action: str, **details) -> None:
        self.bus.publish(f"agent.{self.role}.{action}", session_id=session_id,
                         role=self.role, **details)
