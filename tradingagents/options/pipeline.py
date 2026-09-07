from __future__ import annotations

import hashlib
from typing import Any

from tradingagents.execution.journal import ExecutionJournal
from tradingagents.persistence.protocols import EventJournalPort

from .gateway import AlpacaOptionsGateway, OptionsExecutionGateway
from .models import OptionsExecutionResult, OptionsTradeIntent
from .validator import OptionsRiskPolicy, validate_options_intent


class OptionsExecutionPipeline:
    def __init__(self, gateway: OptionsExecutionGateway, *, policy: OptionsRiskPolicy,
                 journal: EventJournalPort | None = None, is_paper: bool = True):
        self.gateway = gateway
        self.policy = policy
        self.journal = journal or ExecutionJournal()
        self.is_paper = is_paper

    def execute(self, intent: OptionsTradeIntent | dict[str, Any], *, run_id: str | None = None) -> dict:
        try:
            parsed = intent if isinstance(intent, OptionsTradeIntent) else OptionsTradeIntent.model_validate(intent)
        except Exception as exc:
            return {"success": False, "error": f"Invalid options intent: {exc}"}
        client_order_id = "ata-opt-" + hashlib.sha256(parsed.decision_id.encode()).hexdigest()[:20]
        self.journal.append(
            "options_intent_received", symbol=parsed.underlying,
            decision_id=parsed.decision_id, run_id=run_id,
            payload={"intent": parsed.model_dump(mode="json"), "client_order_id": client_order_id},
        )
        errors = validate_options_intent(parsed, self.policy, is_paper=self.is_paper)
        if errors:
            self.journal.append(
                "options_validation_blocked", symbol=parsed.underlying,
                decision_id=parsed.decision_id, run_id=run_id, payload={"errors": errors},
            )
            return OptionsExecutionResult(
                success=False, decision_id=parsed.decision_id, underlying=parsed.underlying,
                gateway=self.gateway.name, intent=parsed, client_order_id=client_order_id,
                validations=errors, error=" ".join(errors),
            ).model_dump(mode="json")
        result = self.gateway.submit(parsed, client_order_id=client_order_id)
        self.journal.append(
            "options_execution_completed" if result.success else "options_broker_rejected",
            symbol=parsed.underlying, decision_id=parsed.decision_id, run_id=run_id,
            payload={"result": result.model_dump(mode="json")},
        )
        return result.model_dump(mode="json")


def execute_autonomous_options_trade(intent: OptionsTradeIntent | dict[str, Any], *,
                                     gateway=None, journal=None, config=None, run_id=None) -> dict:
    if config is None:
        from tradingagents.dataflows.config import get_config
        config = get_config()
    from tradingagents.dataflows.config import get_alpaca_use_paper
    is_paper = str(get_alpaca_use_paper()).strip().lower() in {"1", "true", "yes", "on"}
    policy = OptionsRiskPolicy(
        enabled=bool(config.get("options_autonomous_enabled", False)),
        paper_only=bool(config.get("options_paper_only", True)),
        max_contracts=config.get("options_max_contracts", 2),
        max_loss_usd=config.get("options_max_loss_usd", 500),
        min_days_to_expiration=config.get("options_min_dte", 7),
        max_days_to_expiration=config.get("options_max_dte", 60),
        min_open_interest=config.get("options_min_open_interest", 100),
        max_bid_ask_spread_pct=config.get("options_max_bid_ask_spread_pct", 15),
        allow_undefined_risk=bool(config.get("options_allow_undefined_risk", False)),
    )
    return OptionsExecutionPipeline(
        gateway or AlpacaOptionsGateway(), policy=policy, journal=journal, is_paper=is_paper,
    ).execute(intent, run_id=run_id)
