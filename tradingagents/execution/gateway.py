from __future__ import annotations

from typing import Protocol

from tradingagents.agents.schemas import TradeIntent

from .models import ExecutionPlan, ExecutionResult


class ExecutionGateway(Protocol):
    name: str

    def submit_plan(self, plan: ExecutionPlan, intent: TradeIntent) -> ExecutionResult:
        ...
