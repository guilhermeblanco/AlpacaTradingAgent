from __future__ import annotations

from tradingagents.agents.schemas import TradeIntent

from .models import ExecutionPlan, ExecutionResult


class DryRunExecutionGateway:
    name = "dry-run"

    def submit_plan(self, plan: ExecutionPlan, intent: TradeIntent) -> ExecutionResult:
        return ExecutionResult(
            success=True,
            decision_id=plan.decision_id,
            symbol=plan.symbol,
            gateway=self.name,
            plan=plan,
            actions=[
                {"action": leg.action.value.lower(), "leg": leg.model_dump(mode="json"), "simulated": True}
                for leg in plan.legs
            ],
        )
