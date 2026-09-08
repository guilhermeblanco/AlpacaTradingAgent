from __future__ import annotations

from typing import Any, Callable, Optional

from tradingagents.agents.schemas import TradeIntent
from tradingagents.broker.snapshot import SnapshotProvider
from tradingagents.lifecycle import LifecycleService, LifecycleStatus
from tradingagents.lifecycle.service import DuplicateExecution
from tradingagents.persistence.protocols import EventJournalPort

from .gateway import ExecutionGateway
from .journal import ExecutionJournal, snapshot_hash
from .models import ExecutionResult, PlanAction
from .planner import ExecutionPlanner
from .persistence import ExecutionPersistence
from .validator import validate_intent, validate_plan_semantics, validate_snapshot


class ExecutionPipeline:
    """Single path from typed model intent to validated broker submission."""

    def __init__(
        self,
        snapshot_provider: SnapshotProvider,
        gateway: ExecutionGateway,
        *,
        planner: Optional[ExecutionPlanner] = None,
        journal: Optional[EventJournalPort] = None,
        safety_guard=None,
        risk_sizer: Optional[Callable[..., Any]] = None,
        lifecycle: Optional[LifecycleService] = None,
        unit_of_work_factory: Optional[Callable[[], Any]] = None,
        lifecycle_enabled: bool = True,
        lifecycle_ttl_seconds: int = 900,
    ):
        self.snapshot_provider = snapshot_provider
        self.gateway = gateway
        self.planner = planner or ExecutionPlanner()
        self.journal = journal or ExecutionJournal()
        self.safety_guard = safety_guard
        self.risk_sizer = risk_sizer
        self.lifecycle = lifecycle
        self.persistence = ExecutionPersistence(
            self.journal,
            lifecycle=lifecycle,
            unit_of_work_factory=unit_of_work_factory,
            lifecycle_enabled=lifecycle_enabled,
            lifecycle_ttl_seconds=lifecycle_ttl_seconds,
        )

    def _record(
        self,
        event_type: str,
        intent: TradeIntent,
        run_id: Optional[str],
        *,
        payload: Optional[dict[str, Any]] = None,
        status: Optional[LifecycleStatus] = None,
        error: Optional[str] = None,
        result: Optional[dict[str, Any]] = None,
        execution_result: Optional[ExecutionResult] = None,
    ) -> str:
        return self.persistence.record(
            event_type,
            decision_id=intent.decision_id,
            symbol=intent.symbol,
            run_id=run_id,
            payload=payload,
            status=status,
            error=error,
            result=result,
            execution_result=execution_result,
        )

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

        try:
            lifecycle_record, journal_path = self.persistence.begin(
                decision_id=parsed.decision_id,
                symbol=parsed.symbol,
                run_id=run_id,
                payload={"intent": parsed.model_dump(mode="json")},
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
        errors = validate_intent(parsed, execution_symbol)
        if errors:
            self._record(
                "validation_blocked",
                parsed,
                run_id,
                payload={"errors": errors},
                status=LifecycleStatus.BLOCKED,
                error=" ".join(errors),
            )
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
            self._record(
                "snapshot_failed",
                parsed,
                run_id,
                payload={"error": str(exc)},
                status=LifecycleStatus.FAILED,
                error=str(exc),
            )
            return {
                "success": False,
                "decision_id": parsed.decision_id,
                "symbol": parsed.symbol,
                "error": f"Broker snapshot unavailable; execution failed closed: {exc}",
                "journal_path": journal_path,
            }

        self._record(
            "snapshot_loaded",
            parsed,
            run_id,
            payload={"snapshot_hash": snapshot_hash(portfolio), "captured_at": portfolio.captured_at},
        )
        snapshot_errors = validate_snapshot(parsed, portfolio)
        if snapshot_errors:
            self._record(
                "validation_blocked",
                parsed,
                run_id,
                payload={"stage": "snapshot", "errors": snapshot_errors},
                status=LifecycleStatus.BLOCKED,
                error=" ".join(snapshot_errors),
            )
            return {
                "success": False,
                "decision_id": parsed.decision_id,
                "symbol": parsed.symbol,
                "error": " ".join(snapshot_errors),
                "validations": [{"stage": "snapshot", "allowed": False, "reasons": snapshot_errors}],
                "journal_path": journal_path,
            }
        self._record(
            "intent_validated", parsed, run_id, status=LifecycleStatus.VALIDATED
        )
        plan = self.planner.build_plan(parsed, portfolio, quote, requested_notional_usd)
        if lifecycle_record is not None:
            plan.metadata["idempotency_key"] = lifecycle_record.idempotency_key
            plan.metadata["leg_idempotency_keys"] = [
                f"{lifecycle_record.idempotency_key}-{index}" for index, _ in enumerate(plan.legs)
            ]
        self._record(
            "plan_created",
            parsed,
            run_id,
            payload={"plan": plan.model_dump(mode="json")},
        )
        plan_errors = validate_plan_semantics(parsed, plan)
        if plan_errors:
            self._record(
                "validation_blocked",
                parsed,
                run_id,
                payload={"stage": "plan", "errors": plan_errors},
                status=LifecycleStatus.BLOCKED,
                error=" ".join(plan_errors),
            )
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
                    self._record(
                        "risk_blocked",
                        parsed,
                        run_id,
                        payload={"reason": reason},
                        status=LifecycleStatus.FAILED,
                        error=reason,
                    )
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
                    self._record(
                        "risk_blocked",
                        parsed,
                        run_id,
                        payload={"reason": reason, "sizing": sizing.to_dict()},
                        status=LifecycleStatus.BLOCKED,
                        error=reason,
                    )
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
            self._record(
                "risk_adjusted",
                parsed,
                run_id,
                payload={"plan": plan.model_dump(mode="json")},
            )

        self._record("plan_validated", parsed, run_id, status=LifecycleStatus.PLANNED)
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
                    self._record(
                        "safety_blocked",
                        parsed,
                        run_id,
                        payload={
                            "reasons": verdict.reasons,
                            "checks": verdict.checks,
                        },
                        status=LifecycleStatus.BLOCKED,
                        error=" ".join(verdict.reasons),
                    )
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

        self._record(
            "broker_submitted",
            parsed,
            run_id,
            payload={
                "gateway": self.gateway.name,
                "idempotency_key": plan.metadata.get("idempotency_key"),
            },
            status=LifecycleStatus.SUBMITTING,
        )
        result = self.gateway.submit_plan(plan, parsed)
        result.validations = validations
        result.journal_path = journal_path
        has_remote_order = any(
            (action.get("result", action) or {}).get("order_id")
            for action in result.actions
        )
        submission_only = result.success and not plan.is_noop and bool(has_remote_order)
        event = (
            "execution_submitted"
            if submission_only
            else "execution_completed"
            if result.success
            else "broker_rejected"
        )
        if guard is not None and guard.enabled and not plan.is_noop:
            guard.record_order_result(result.success)
        result_payload = result.model_dump(mode="json")
        self._record(
            event,
            parsed,
            run_id,
            payload={"result": result_payload},
            status=(
                LifecycleStatus.SUBMITTED
                if submission_only
                else LifecycleStatus.SUCCEEDED
                if result.success
                else LifecycleStatus.FAILED
            ),
            error=result.error,
            result=result_payload,
            execution_result=result if has_remote_order else None,
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
    unit_of_work_factory=None,
    run_id: Optional[str] = None,
) -> dict[str, Any]:
    config = None
    runtime = None
    if (
        snapshot_provider is None
        or gateway is None
        or journal is None
        or risk_sizer is None
        or lifecycle is None
        or unit_of_work_factory is None
    ):
        try:
            from tradingagents.dataflows.config import get_config

            config = get_config() or {}
        except Exception:
            config = {}
    if snapshot_provider is None or gateway is None:
        from tradingagents.broker.registry import default_broker_registry

        broker_name = str(config.get("execution_broker", "alpaca")).lower()
        runtime = default_broker_registry().create(broker_name, config)
        if snapshot_provider is None:
            snapshot_provider = runtime.snapshot_provider
        if gateway is None:
            gateway = runtime.execution_gateway
        if str(config.get("execution_gateway", "alpaca")).lower() == "dry-run":
            from .dry_run_gateway import DryRunExecutionGateway

            gateway = DryRunExecutionGateway()
    if unit_of_work_factory is None:
        from tradingagents.persistence import build_persistence_runtime

        runtime = build_persistence_runtime(config or {})
        unit_of_work_factory = runtime.unit_of_work_factory
    if journal is None:
        journal = ExecutionJournal((config or {}).get("results_dir", "eval_results"))
    if (
        lifecycle is None
        and unit_of_work_factory is None
        and (config or {}).get("lifecycle_enabled", True)
    ):
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
    try:
        return ExecutionPipeline(
            snapshot_provider,
            gateway,
            journal=journal,
            safety_guard=safety_guard,
            risk_sizer=risk_sizer,
            lifecycle=lifecycle,
            unit_of_work_factory=unit_of_work_factory,
            lifecycle_enabled=(config or {}).get("lifecycle_enabled", True),
            lifecycle_ttl_seconds=(config or {}).get(
                "lifecycle_intent_ttl_seconds", 900
            ),
        ).execute(symbol, trade_intent, requested_notional_usd, run_id=run_id)
    finally:
        if runtime is not None:
            runtime.close()
