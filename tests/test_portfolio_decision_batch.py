from __future__ import annotations

from tradingagents.agents.schemas import (
    ExecutableAction,
    IntentType,
    RiskDecision,
    build_trade_intent_from_risk_decision,
)
from tradingagents.broker.models import (
    AccountSnapshot,
    PortfolioSnapshot,
    PositionSnapshot,
)
from tradingagents.orchestration import (
    BatchOrchestrator,
    Candidate,
    ProviderPolicy,
    run_portfolio_decision_batch,
)
from tradingagents.portfolio import PortfolioLimitsConfig
from tradingagents.portfolio.batch import (
    BatchAllocationStatus,
    PortfolioIntentRequest,
    allocate_intent_batch,
)


def _intent(
    symbol: str,
    *,
    intent_type: IntentType = IntentType.OPEN,
    confidence: float = 0.5,
    target_pct: float | None = None,
    max_notional: float | None = None,
):
    action = ExecutableAction.SELL if intent_type in {IntentType.REDUCE, IntentType.CLOSE} else ExecutableAction.BUY
    return build_trade_intent_from_risk_decision(
        symbol=symbol,
        trading_mode="investment",
        current_position="LONG" if action == ExecutableAction.SELL else "NEUTRAL",
        decision=RiskDecision(
            action=action,
            intent_type=intent_type,
            confidence="high",
            confidence_score=confidence,
            risk_rationale="batch test",
            required_controls="test",
            target_portfolio_pct=target_pct,
            max_notional_usd=max_notional,
        ),
    )


def _snapshot(*positions: tuple[str, float]) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        account=AccountSnapshot(equity=100_000),
        positions=[
            PositionSnapshot(symbol=symbol, quantity=1, market_value=value)
            for symbol, value in positions
        ],
        captured_at="2026-09-07T15:00:00+00:00",
        broker="test",
    )


def _limits(**overrides) -> PortfolioLimitsConfig:
    values = {"vol_sizing_enabled": False, "max_gross_exposure_pct": 100}
    values.update(overrides)
    return PortfolioLimitsConfig(**values)


def test_batch_reserves_shared_headroom_in_priority_order() -> None:
    low = PortfolioIntentRequest(
        intent=_intent("LOW", confidence=0.4),
        requested_notional_usd=15_000,
        candidate_score=9,
    )
    high = PortfolioIntentRequest(
        intent=_intent("HIGH", confidence=0.9),
        requested_notional_usd=15_000,
        candidate_score=1,
    )

    batch = allocate_intent_batch(
        [low, high],
        _snapshot(("AAPL", 80_000)),
        {},
        limits=_limits(),
        max_symbol_concentration_pct=100,
    )
    allocations = {row.symbol: row for row in batch.allocations}

    assert allocations["HIGH"].priority == 1
    assert allocations["HIGH"].approved_notional_usd == 15_000
    assert allocations["LOW"].approved_notional_usd == 5_000
    assert batch.ending_reserved_exposure_usd == 100_000


def test_batch_enforces_target_notional_and_symbol_concentration() -> None:
    first = PortfolioIntentRequest(
        intent=_intent("MSFT", confidence=0.9, target_pct=25),
        requested_notional_usd=20_000,
    )
    second = PortfolioIntentRequest(
        intent=_intent("MSFT", confidence=0.8, target_pct=25),
        requested_notional_usd=20_000,
    )
    batch = allocate_intent_batch(
        [second, first],
        _snapshot(("MSFT", 20_000)),
        {},
        limits=_limits(),
        max_symbol_concentration_pct=25,
    )

    additions = sorted(batch.allocations, key=lambda row: row.priority)
    assert additions[0].approved_notional_usd == 5_000
    assert additions[1].approved_notional_usd == 0
    assert additions[1].status is BatchAllocationStatus.BLOCKED


def test_risk_reducing_intent_bypasses_exhausted_headroom() -> None:
    request = PortfolioIntentRequest(
        intent=_intent("AAPL", intent_type=IntentType.REDUCE),
        requested_notional_usd=10_000,
    )
    batch = allocate_intent_batch(
        [request], _snapshot(("AAPL", 100_000)), {}, limits=_limits()
    )
    allocation = batch.allocations[0]
    assert allocation.status is BatchAllocationStatus.RISK_REDUCING
    assert allocation.approved_notional_usd == 10_000


def test_max_notional_is_a_hard_ceiling() -> None:
    request = PortfolioIntentRequest(
        intent=_intent("NVDA", max_notional=3_000),
        requested_notional_usd=10_000,
    )
    batch = allocate_intent_batch(
        [request], _snapshot(), {}, limits=_limits(), max_symbol_concentration_pct=100
    )
    assert batch.allocations[0].approved_notional_usd == 3_000


def test_symbol_aliases_share_the_same_concentration_budget() -> None:
    request = PortfolioIntentRequest(
        intent=_intent("BTC/USD", target_pct=25),
        requested_notional_usd=10_000,
    )
    batch = allocate_intent_batch(
        [request],
        _snapshot(("BTCUSD", 20_000)),
        {},
        limits=_limits(),
        max_symbol_concentration_pct=25,
    )
    assert batch.allocations[0].approved_notional_usd == 5_000


def test_disabled_soft_adjustments_keep_hard_concentration_limit() -> None:
    request = PortfolioIntentRequest(
        intent=_intent("AAPL"), requested_notional_usd=20_000
    )
    batch = allocate_intent_batch(
        [request],
        _snapshot(("AAPL", 20_000)),
        {},
        limits=_limits(enabled=False),
        max_symbol_concentration_pct=25,
    )
    assert batch.gross_limit_usd is None
    assert batch.allocations[0].approved_notional_usd == 5_000


def test_orchestration_captures_one_snapshot_and_excludes_failed_analysis() -> None:
    candidates = [
        Candidate(symbol="AAPL", score=8, source="test"),
        Candidate(symbol="FAIL", score=7, source="test"),
    ]

    def analyze(candidate):
        if candidate.symbol == "FAIL":
            raise RuntimeError("analysis failed")
        return {"trade_intent": _intent(candidate.symbol)}

    class Provider:
        calls = 0

        def get_portfolio_snapshot(self):
            self.calls += 1
            return _snapshot()

    provider = Provider()
    run = run_portfolio_decision_batch(
        BatchOrchestrator(
            max_workers=2,
            provider_policies={"test": ProviderPolicy(max_retries=0)},
        ),
        candidates,
        analyze,
        provider="test",
        snapshot_provider=provider,
        price_history={},
        requested_notional=lambda candidate, intent: 5_000,
        limits=_limits(),
    )

    assert provider.calls == 1
    assert len(run.analysis_results) == 2
    assert [row.symbol for row in run.decision_batch.allocations] == ["AAPL"]
