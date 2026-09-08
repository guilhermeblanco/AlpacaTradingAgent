from __future__ import annotations

from typing import Any, Protocol

from tradingagents.agents.schemas import TradeIntent

from .models import ExecutionPlan, ExecutionResult


class ExecutionGateway(Protocol):
    name: str

    def submit_plan(self, plan: ExecutionPlan, intent: TradeIntent) -> ExecutionResult:
        ...


class SubmissionUncertain(RuntimeError):
    """The request may have reached the broker but no response was received."""

    def __init__(
        self,
        message: str,
        *,
        gateway: str,
        leg_index: int,
        actions: list[dict[str, Any]],
    ):
        super().__init__(message)
        self.gateway = gateway
        self.leg_index = leg_index
        self.actions = actions


def is_uncertain_submission_error(exc: BaseException) -> bool:
    """Conservatively identify failures that can occur after request transmission."""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return (
        any(
            token in name
            for token in (
                "timeout",
                "connection",
                "connecterror",
                "network",
                "protocol",
                "readerror",
                "writeerror",
            )
        )
        or any(
            token in message
            for token in (
                "timed out",
                "timeout",
                "connection reset",
                "connection aborted",
                "remote disconnected",
                "broken pipe",
            )
        )
    )
