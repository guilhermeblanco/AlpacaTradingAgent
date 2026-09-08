"""Headless discovery-to-execution cycles with durable admission and reservations."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import pandas as pd

from tradingagents.agents.schemas import TradeIntent
from tradingagents.broker.models import PortfolioSnapshot
from tradingagents.broker.snapshot import SnapshotProvider
from tradingagents.portfolio import PortfolioLimitsConfig

from .batch import BatchOrchestrator, Candidate
from .execution_coordinator import PortfolioDispatchResult, ReservationAwareExecutionCoordinator
from .portfolio_batch import PortfolioBatchRun, run_portfolio_decision_batch


LOGGER = logging.getLogger(__name__)


class MarketSessionGate:
    """Asset-session policy independent of the selected execution broker."""

    def __init__(self, equity_calendar: str = "XNYS"):
        import exchange_calendars

        self.equity_calendar = exchange_calendars.get_calendar(equity_calendar)

    def is_open(self, asset_class: str, *, now: datetime) -> bool:
        normalized = asset_class.lower().strip()
        if normalized in {"crypto", "cryptocurrency"}:
            return True
        minute = pd.Timestamp(now.astimezone(timezone.utc)).floor("min")
        return bool(self.equity_calendar.is_open_on_minute(minute))


@dataclass
class AutonomousCycleResult:
    started_at: datetime
    finished_at: datetime
    discovered: int = 0
    admitted: int = 0
    session_blocked: int = 0
    capability_blocked: int = 0
    admission_blocked: int = 0
    portfolio_run: Optional[PortfolioBatchRun] = None
    dispatch: Optional[PortfolioDispatchResult] = None
    errors: list[str] = field(default_factory=list)


class AutonomousCycleScheduler:
    def __init__(
        self,
        *,
        snapshot_provider: SnapshotProvider,
        candidate_source: Callable[[PortfolioSnapshot], list[Candidate]],
        analysis_handler: Callable[[Candidate], Any],
        price_history_loader: Callable[[list[str]], dict[str, pd.DataFrame]],
        requested_notional: Callable[[Candidate, TradeIntent], float],
        execution_coordinator: ReservationAwareExecutionCoordinator,
        unit_of_work_factory: Callable[[], Any],
        account_key: str,
        analysis_provider: str,
        allowed_asset_classes: set[str],
        limits: Optional[PortfolioLimitsConfig] = None,
        max_symbol_concentration_pct: float = 25.0,
        max_candidates: int = 3,
        orchestrator: Optional[BatchOrchestrator] = None,
        session_gate: Optional[MarketSessionGate] = None,
        estimated_tokens_per_analysis: int = 0,
        reservation_ttl_seconds: int = 300,
    ):
        if not account_key.strip():
            raise ValueError("account_key is required for durable portfolio reservations")
        self.snapshot_provider = snapshot_provider
        self.candidate_source = candidate_source
        self.analysis_handler = analysis_handler
        self.price_history_loader = price_history_loader
        self.requested_notional = requested_notional
        self.execution_coordinator = execution_coordinator
        self.unit_of_work_factory = unit_of_work_factory
        self.account_key = account_key
        self.analysis_provider = analysis_provider
        self.allowed_asset_classes = {
            self._asset_class(value) for value in allowed_asset_classes
        }
        self.limits = limits or PortfolioLimitsConfig()
        self.max_symbol_concentration_pct = max_symbol_concentration_pct
        self.max_candidates = max(1, int(max_candidates))
        self.orchestrator = orchestrator or BatchOrchestrator()
        self.session_gate = session_gate or MarketSessionGate()
        self.estimated_tokens_per_analysis = max(0, int(estimated_tokens_per_analysis))
        self.reservation_ttl_seconds = max(1, int(reservation_ttl_seconds))
        self._stop = threading.Event()

    @staticmethod
    def _asset_class(value: str) -> str:
        normalized = value.lower().strip()
        return "equity" if normalized in {"stock", "stocks"} else normalized

    def request_stop(self) -> None:
        self._stop.set()
        self.orchestrator.stop()

    def run_once(self, *, now: Optional[datetime] = None) -> AutonomousCycleResult:
        started_at = now or datetime.now(timezone.utc)
        result = AutonomousCycleResult(started_at=started_at, finished_at=started_at)
        admitted: list[Candidate] = []
        try:
            snapshot = self.snapshot_provider.get_portfolio_snapshot()
            candidates = sorted(
                self.candidate_source(snapshot),
                key=lambda row: (-row.score, row.symbol),
            )
            result.discovered = len(candidates)
            eligible = []
            for candidate in candidates:
                asset_class = self._asset_class(candidate.asset_class)
                if asset_class not in self.allowed_asset_classes:
                    result.capability_blocked += 1
                    continue
                if not self.session_gate.is_open(asset_class, now=started_at):
                    result.session_blocked += 1
                    continue
                eligible.append(candidate)

            for candidate in eligible[: self.max_candidates]:
                price = candidate.provenance.get("price")
                with self.unit_of_work_factory() as uow:
                    decision = uow.admission.try_admit(
                        candidate.symbol,
                        price=float(price) if price is not None else None,
                        estimated_tokens=self.estimated_tokens_per_analysis,
                        now=started_at,
                    )
                    uow.commit()
                if decision.allowed:
                    admitted.append(candidate)
                else:
                    result.admission_blocked += 1
            result.admitted = len(admitted)
            if not admitted or self._stop.is_set():
                return result

            history_symbols = list(
                dict.fromkeys(
                    [candidate.symbol for candidate in admitted]
                    + [position.symbol for position in snapshot.positions]
                )
            )
            history = self.price_history_loader(history_symbols)
            portfolio_run = run_portfolio_decision_batch(
                self.orchestrator,
                admitted,
                self.analysis_handler,
                provider=self.analysis_provider,
                snapshot_provider=self.snapshot_provider,
                price_history=history,
                requested_notional=self.requested_notional,
                limits=self.limits,
                max_symbol_concentration_pct=self.max_symbol_concentration_pct,
            )
            result.portfolio_run = portfolio_run
            intents: dict[str, TradeIntent] = {}
            for analysis in portfolio_run.analysis_results:
                if not analysis.success or analysis.cancelled:
                    continue
                value = analysis.value
                if isinstance(value, dict) and ("trade_intent" in value or "intent" in value):
                    value = value.get("trade_intent", value.get("intent"))
                intent = value if isinstance(value, TradeIntent) else TradeIntent.model_validate(value)
                intents[intent.decision_id] = intent
            if not intents:
                return result
            result.dispatch = self.execution_coordinator.execute(
                portfolio_run.decision_batch,
                intents,
                account_key=self.account_key,
                reservation_ttl_seconds=self.reservation_ttl_seconds,
                run_id=portfolio_run.decision_batch.batch_id,
                now=started_at,
            )
        except Exception as exc:
            result.errors.append(str(exc))
            LOGGER.exception("Autonomous cycle failed")
        finally:
            for candidate in admitted:
                try:
                    with self.unit_of_work_factory() as uow:
                        uow.admission.complete(candidate.symbol)
                        uow.commit()
                except Exception as exc:
                    result.errors.append(
                        f"failed to complete admission for {candidate.symbol}: {exc}"
                    )
                    LOGGER.exception("Failed to complete admission for %s", candidate.symbol)
            result.finished_at = datetime.now(timezone.utc)
        return result

    def run_forever(self, *, interval_seconds: float) -> None:
        interval_seconds = max(1.0, float(interval_seconds))
        while not self._stop.is_set():
            result = self.run_once()
            LOGGER.info(
                "autonomous cycle discovered=%s admitted=%s dispatched=%s errors=%s",
                result.discovered,
                result.admitted,
                len(result.dispatch.allocations) if result.dispatch else 0,
                len(result.errors),
            )
            self._stop.wait(interval_seconds)
