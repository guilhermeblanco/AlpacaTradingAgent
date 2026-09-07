from __future__ import annotations

from typing import Any, Callable, Optional

from tradingagents.agents.schemas import TradeIntent
from tradingagents.broker.snapshot import SnapshotProvider
from tradingagents.lifecycle import LifecycleService, LifecycleStatus
from tradingagents.lifecycle.service import DuplicateExecution

from .gateway import ExecutionGateway
from .journal import ExecutionJournal, snapshot_hash
from .models import ExecutionResult, PlanAction
from .planner import ExecutionPlanner
from .validator import validate_intent, validate_plan_semantics, validate_snapshot


class ExecutionPipeline:
    """Single path from typed model intent to validated broker submission."""

    def __init__(
        self,
        snapshot_provider: SnapshotProvider,
        gateway: ExecutionGateway,
        *,
        planner: Optional[ExecutionPlanner] = None,
        journal: Optional[ExecutionJournal] = None,
        safety_guard=None,
        risk_sizer: Optional[Callable[..., Any]] = None,
        lifecycle: Optional[LifecycleService] = None,
    ):
        self.snapshot_provider = snapshot_provider
        self.gateway = gateway
        self.planner = planner or ExecutionPlanner()
        self.journal = journal or ExecutionJournal()
        self.safety_guard = safety_guard
        self.risk_sizer = risk_sizer
        self.lifecycle = lifecycle

    def _transition(self, decision_id: str, status: LifecycleStatus, **kwargs) -> None:
        if self.lifecycle is not None:
            self.lifecycle.transition(decision_id, status, **kwargs)

    def execute(
        self,
        execution_symbol: str,
        intent: TradeIntent | dict[str, Any],
        requested_notional_usd: float,
        *,
        run_id: Optional[str] = None,
    ) -> dict[str, Any]:
        try:
            parsed = intent if isinstance(intent, TradeIntent) else TradeIntent.model_validate(intent)
        except Exception as exc:
            return {"success": False, "error": f"Invalid trade intent: {exc}"}

        lifecycle_record = None
        if self.lifecycle is not None:
            try:
                lifecycle_record = self.lifecycle.begin(
                    decision_id=parsed.decision_id,
                    symbol=parsed.symbol,
                    run_id=run_id,
                )
            except DuplicateExecution as exc:
                if exc.record.result is not None:
                    return exc.record.result
                return {
                    "success": False,
                    "decision_id": parsed.decision_id,
                    "symbol": parsed.symbol,
                    "error": str(exc),
                    "duplicate": True,
                    "lifecycle_status": exc.record.status.value,
                }

        journal_path = self.journal.append(
            "intent_received",
            symbol=parsed.symbol,
            decision_id=parsed.decision_id,
            run_id=run_id,
            payload={"intent": parsed.model_dump(mode="json")},
        )
        errors = validate_intent(parsed, execution_symbol)
        if errors:
            self._transition(parsed.decision_id, LifecycleStatus.BLOCKED, error=" ".join(errors))
            self.journal.append("validation_blocked", symbol=parsed.symbol, decision_id=parsed.decision_id, run_id=run_id, payload={"errors": errors})
            return {
                "success": False,
                "decision_id": parsed.decision_id,
                "symbol": parsed.symbol,
                "error": " ".join(errors),
                "validations": [{"stage": "intent", "allowed": False, "reasons": errors}],
                "journal_path": journal_path,
            }

        try:
            portfolio = self.snapshot_provider.get_portfolio_snapshot()
            quote = self.snapshot_provider.get_quote_snapshot(parsed.symbol)
        except Exception as exc:
            self._transition(parsed.decision_id, LifecycleStatus.FAILED, error=str(exc))
            self.journal.append("snapshot_failed", symbol=parsed.symbol, decision_id=parsed.decision_id, run_id=run_id, payload={"error": str(exc)})
            return {
                "success": False,
                "decision_id": parsed.decision_id,
                "symbol": parsed.symbol,
                "error": f"Broker snapshot unavailable; execution failed closed: {exc}",
                "journal_path": journal_path,
            }

        self.journal.append(
            "snapshot_loaded",
            symbol=parsed.symbol,
            decision_id=parsed.decision_id,
            run_id=run_id,
            payload={"snapshot_hash": snapshot_hash(portfolio), "captured_at": portfolio.captured_at},
        )
        snapshot_errors = validate_snapshot(parsed, portfolio)
        if snapshot_errors:
            self._transition(parsed.decision_id, LifecycleStatus.BLOCKED, error=" ".join(snapshot_errors))
            self.journal.append("validation_blocked", symbol=parsed.symbol, decision_id=parsed.decision_id, run_id=run_id, payload={"stage": "snapshot", "errors": snapshot_errors})
            return {
                "success": False,
                "decision_id": parsed.decision_id,
                "symbol": parsed.symbol,
                "error": " ".join(snapshot_errors),
                "validations": [{"stage": "snapshot", "allowed": False, "reasons": snapshot_errors}],
                "journal_path": journal_path,
            }
        self._transition(parsed.decision_id, LifecycleStatus.VALIDATED)
        plan = self.planner.build_plan(parsed, portfolio, quote, requested_notional_usd)
        if lifecycle_record is not None:
            plan.metadata["idempotency_key"] = lifecycle_record.idempotency_key
            plan.metadata["leg_idempotency_keys"] = [
                f"{lifecycle_record.idempotency_key}-{index}" for index, _ in enumerate(plan.legs)
            ]
        self.journal.append("plan_created", symbol=parsed.symbol, decision_id=parsed.decision_id, run_id=run_id, payload={"plan": plan.model_dump(mode="json")})
        plan_errors = validate_plan_semantics(parsed, plan)
        if plan_errors:
            self._transition(parsed.decision_id, LifecycleStatus.BLOCKED, error=" ".join(plan_errors))
            self.journal.append("validation_blocked", symbol=parsed.symbol, decision_id=parsed.decision_id, run_id=run_id, payload={"stage": "plan", "errors": plan_errors})
            return {
                "success": False,
                "decision_id": parsed.decision_id,
                "symbol": parsed.symbol,
                "error": " ".join(plan_errors),
                "plan": plan.model_dump(mode="json"),
                "validations": [{"stage": "plan", "allowed": False, "reasons": plan_errors}],
                "journal_path": journal_path,
            }

        if self.risk_sizer is not None:
            for leg in plan.legs:
                if leg.action == PlanAction.HOLD or leg.risk_reducing:
                    continue
                try:
                    sizing = self.risk_sizer(
                        symbol=parsed.symbol,
                        confidence=parsed.confidence,
                        requested_notional=leg.notional_usd,
                        side=leg.side or "buy",
                    )
                except Exception as exc:
                    reason = f"Risk sizing unavailable; execution failed closed: {exc}"
                    self._transition(parsed.decision_id, LifecycleStatus.FAILED, error=reason)
                    self.journal.append("risk_blocked", symbol=parsed.symbol, decision_id=parsed.decision_id, run_id=run_id, payload={"reason": reason})
                    return {
                        "success": False,
                        "decision_id": parsed.decision_id,
                        "symbol": parsed.symbol,
                        "error": reason,
                        "plan": plan.model_dump(mode="json"),
                        "journal_path": journal_path,
                    }
                if not sizing.approved:
                    reason = f"Trade blocked by deterministic risk engine: {sizing.reason}"
                    self._transition(parsed.decision_id, LifecycleStatus.BLOCKED, error=reason)
                    self.journal.append("risk_blocked", symbol=parsed.symbol, decision_id=parsed.decision_id, run_id=run_id, payload={"reason": reason, "sizing": sizing.to_dict()})
                    return {
                        "success": False,
                        "decision_id": parsed.decision_id,
                        "symbol": parsed.symbol,
                        "error": reason,
                        "plan": plan.model_dump(mode="json"),
                        "journal_path": journal_path,
                    }
                leg.notional_usd = min(leg.notional_usd, sizing.notional)
                leg.quantity = leg.notional_usd / quote.reference_price if quote.reference_price else None
                plan.metadata.setdefault("risk_sizing", []).append(sizing.to_dict())
            self.journal.append("risk_adjusted", symbol=parsed.symbol, decision_id=parsed.decision_id, run_id=run_id, payload={"plan": plan.model_dump(mode="json")})

        self._transition(parsed.decision_id, LifecycleStatus.PLANNED)
        validations = [{"stage": "intent", "allowed": True, "reasons": []}]
        guard = self.safety_guard
        if guard is None:
            try:
                from tradingagents.safety import get_safety_guard

                guard = get_safety_guard()
            except Exception:
                guard = None

        if guard is not None and guard.enabled:
            position = portfolio.position_for(parsed.symbol)
            account = {
                "equity": portfolio.account.equity,
                "last_equity": portfolio.account.last_equity,
            }
            position_value = abs(position.market_value) if position else 0.0
            for leg in plan.legs:
                if leg.action == PlanAction.HOLD:
                    continue
                verdict = guard.check_order(
                    parsed.symbol,
                    leg.notional_usd,
                    account=account,
                    position_value=position_value,
                    risk_reducing=leg.risk_reducing,
                )
                validations.append({"stage": "safety", "allowed": verdict.allowed, "reasons": verdict.reasons, "checks": verdict.checks})
                if not verdict.allowed:
                    self._transition(
                        parsed.decision_id,
                        LifecycleStatus.BLOCKED,
                        error=" ".join(verdict.reasons),
                    )
                    self.journal.append("safety_blocked", symbol=parsed.symbol, decision_id=parsed.decision_id, run_id=run_id, payload={"reasons": verdict.reasons, "checks": verdict.checks})
                    return ExecutionResult(
                        success=False,
                        decision_id=parsed.decision_id,
                        symbol=parsed.symbol,
                        gateway=self.gateway.name,
                        plan=plan,
                        validations=validations,
                        error="Safety layer blocked order flow: " + " ".join(verdict.reasons),
                        safety_blocked=True,
                        journal_path=journal_path,
                    ).model_dump(mode="json")
                if leg.action == PlanAction.CLOSE:
                    position_value = 0.0
                elif leg.risk_reducing:
                    position_value = max(0.0, position_value - leg.notional_usd)
                else:
                    position_value += leg.notional_usd

        self._transition(parsed.decision_id, LifecycleStatus.SUBMITTING)
        self.journal.append("broker_submitted", symbol=parsed.symbol, decision_id=parsed.decision_id, run_id=run_id, payload={"gateway": self.gateway.name, "idempotency_key": plan.metadata.get("idempotency_key")})
        result = self.gateway.submit_plan(plan, parsed)
        result.validations = validations
        result.journal_path = journal_path
        event = "execution_completed" if result.success else "broker_rejected"
        self.journal.append(event, symbol=parsed.symbol, decision_id=parsed.decision_id, run_id=run_id, payload={"result": result.model_dump(mode="json")})
        if guard is not None and guard.enabled and not plan.is_noop:
            guard.record_order_result(result.success)
        result_payload = result.model_dump(mode="json")
        self._transition(
            parsed.decision_id,
            LifecycleStatus.SUCCEEDED if result.success else LifecycleStatus.FAILED,
            error=result.error,
            result=result_payload,
        )
        return result_payload


def execute_autonomous_trade(
    symbol: str,
    trade_intent: TradeIntent | dict[str, Any],
    requested_notional_usd: float,
    *,
    snapshot_provider=None,
    gateway=None,
    journal=None,
    safety_guard=None,
    risk_sizer=None,
    lifecycle=None,
    run_id: Optional[str] = None,
) -> dict[str, Any]:
    if snapshot_provider is None:
        from tradingagents.broker.alpaca_snapshot import AlpacaSnapshotProvider

        snapshot_provider = AlpacaSnapshotProvider()
    config = None
    if gateway is None or journal is None or risk_sizer is None or lifecycle is None:
        try:
            from tradingagents.dataflows.config import get_config

            config = get_config() or {}
        except Exception:
            config = {}
    if gateway is None:
        from tradingagents.dataflows.config import get_alpaca_use_paper

        if str(config.get("execution_gateway", "alpaca")).lower() == "dry-run":
            from .dry_run_gateway import DryRunExecutionGateway

            gateway = DryRunExecutionGateway()
        else:
            from .alpaca_gateway import AlpacaExecutionGateway, AlpacaPaperExecutionGateway

            use_paper = str(get_alpaca_use_paper()).strip().lower() in {"1", "true", "yes", "on"}
            gateway = AlpacaPaperExecutionGateway() if use_paper else AlpacaExecutionGateway()
    if journal is None:
        journal = ExecutionJournal((config or {}).get("results_dir", "eval_results"))
    if lifecycle is None and (config or {}).get("lifecycle_enabled", True):
        from pathlib import Path

        from tradingagents.lifecycle import LifecycleRepository

        results_dir = Path((config or {}).get("results_dir", "eval_results"))
        lifecycle = LifecycleService(
            LifecycleRepository(
                (config or {}).get("lifecycle_db_path")
                or results_dir / "execution_lifecycle.sqlite3"
            ),
            default_ttl_seconds=(config or {}).get("lifecycle_intent_ttl_seconds", 900),
        )
    if risk_sizer is None and (config or {}).get("risk_sizing_enabled"):
        from tradingagents.dataflows.alpaca_utils import AlpacaUtils

        risk_params = dict((config or {}).get("risk_sizing_params") or {})

        def risk_sizer(**kwargs):
            return AlpacaUtils.compute_risk_sized_amount(
                risk_params=risk_params,
                **kwargs,
            )
    return ExecutionPipeline(
        snapshot_provider,
        gateway,
        journal=journal,
        safety_guard=safety_guard,
        risk_sizer=risk_sizer,
        lifecycle=lifecycle,
    ).execute(symbol, trade_intent, requested_notional_usd, run_id=run_id)
