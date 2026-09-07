import unittest

from tradingagents.agents.schemas import ExecutableAction, RiskDecision, build_trade_intent_from_risk_decision
from tradingagents.broker.registry import BrokerCapabilities, BrokerRegistry, BrokerRuntime
from tradingagents.broker.robinhood import RobinhoodExecutionGateway, RobinhoodSnapshotProvider
from tradingagents.broker.tradier import TradierClient, TradierExecutionGateway, TradierSnapshotProvider
from tradingagents.execution.dry_run_gateway import DryRunExecutionGateway
from tradingagents.execution.models import ExecutionLeg, ExecutionPlan, PlanAction


class FakeTradierTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, path, *, params=None, data=None):
        self.calls.append((method, path, params, data))
        if path.endswith("/balances"):
            return {"balances": {"total_equity": 100000, "total_cash": 50000,
                                  "stock_buying_power": 50000}}
        if path.endswith("/positions"):
            return {"positions": {"position": {"symbol": "AAPL", "quantity": 10,
                                                  "cost_basis": 900}}}
        if path == "/markets/quotes":
            return {"quotes": {"quote": {"symbol": "AAPL", "bid": 99, "ask": 101, "last": 100}}}
        if path.endswith("/orders"):
            return {"order": {"id": 123, "status": "ok"}}
        raise AssertionError(path)


class FakeRobinhoodClient:
    def __init__(self):
        self.calls = []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "get_accounts":
            return {"accounts": [{"account_number": "RH123", "state": "active",
                                   "agentic_allowed": True}]}
        if name == "get_portfolio":
            return {"total_value": "100000", "cash": "50000",
                    "buying_power": {"buying_power": "50000"}}
        if name == "get_equity_positions":
            return {"positions": [{"symbol": "AAPL", "quantity": "10",
                                    "current_price": "100", "market_value": "1000"}]}
        if name == "get_equity_quotes":
            return {"results": [{"symbol": "AAPL", "bid_price": "99", "ask_price": "101"}]}
        if name == "review_equity_order":
            return {"estimated_total": "1000"}
        if name == "place_equity_order":
            return {"order_id": "rh-order-1"}
        raise AssertionError(name)


def trade_intent():
    return build_trade_intent_from_risk_decision(
        symbol="AAPL", trading_mode="investment", current_position="NEUTRAL",
        decision=RiskDecision(action=ExecutableAction.BUY, confidence="high",
                              risk_rationale="test", required_controls="test"),
    )


def execution_plan(quantity=10):
    return ExecutionPlan(
        decision_id="d1", symbol="AAPL", intent_schema_version="2.0",
        current_allocation_pct=0, current_notional_usd=0, delta_notional_usd=1000,
        reference_price=100,
        legs=[ExecutionLeg(action=PlanAction.BUY, side="buy", notional_usd=1000,
                           quantity=quantity, reason="test")],
        metadata={"leg_idempotency_keys": ["ata-d1-0"]},
    )


class MultiBrokerTests(unittest.TestCase):
    def test_registry_creates_named_runtime(self):
        registry = BrokerRegistry()
        marker = object()
        registry.register("test", lambda config: BrokerRuntime(
            name="test", capabilities=BrokerCapabilities(paper_trading=True),
            snapshot_provider=marker, execution_gateway=DryRunExecutionGateway(),
        ))
        self.assertEqual(registry.create("TEST").snapshot_provider, marker)
        self.assertEqual(registry.names(), ["test"])

    def test_tradier_snapshot_maps_balances_positions_and_quotes(self):
        transport = FakeTradierTransport()
        provider = TradierSnapshotProvider(TradierClient(
            token="token", account_id="account", transport=transport,
        ))
        snapshot = provider.get_portfolio_snapshot()
        self.assertEqual(snapshot.broker, "tradier")
        self.assertEqual(snapshot.account.equity, 100000)
        self.assertEqual(snapshot.positions[0].market_value, 1000)

    def test_tradier_gateway_submits_whole_share_order_with_tag(self):
        transport = FakeTradierTransport()
        gateway = TradierExecutionGateway(TradierClient(
            token="token", account_id="account", transport=transport,
        ))
        result = gateway.submit_plan(execution_plan(), trade_intent())
        self.assertTrue(result.success)
        order_call = transport.calls[-1]
        self.assertEqual(order_call[3]["tag"], "ata-d1-0")
        self.assertEqual(order_call[3]["quantity"], 10)

    def test_tradier_rejects_fractional_only_plan(self):
        gateway = TradierExecutionGateway(TradierClient(
            token="token", account_id="account", transport=FakeTradierTransport(),
        ))
        self.assertFalse(gateway.submit_plan(execution_plan(quantity=0.5), trade_intent()).success)

    def test_robinhood_snapshot_uses_agentic_account(self):
        provider = RobinhoodSnapshotProvider(FakeRobinhoodClient())
        snapshot = provider.get_portfolio_snapshot()
        self.assertEqual(snapshot.broker, "robinhood")
        self.assertEqual(snapshot.account.equity, 100000)
        self.assertEqual(snapshot.positions[0].quantity, 10)

    def test_robinhood_defaults_to_review_only(self):
        client = FakeRobinhoodClient()
        result = RobinhoodExecutionGateway(client).submit_plan(execution_plan(), trade_intent())
        self.assertTrue(result.success)
        self.assertTrue(result.actions[0]["result"]["review_only"])
        self.assertNotIn("place_equity_order", [name for name, _ in client.calls])

    def test_robinhood_live_submission_requires_two_explicit_controls(self):
        client = FakeRobinhoodClient()
        blocked = RobinhoodExecutionGateway(client, review_only=False).submit_plan(
            execution_plan(), trade_intent()
        )
        allowed = RobinhoodExecutionGateway(
            client, review_only=False, live_orders_enabled=True,
        ).submit_plan(execution_plan(), trade_intent())
        self.assertFalse(blocked.success)
        self.assertTrue(allowed.success)
        self.assertEqual(allowed.actions[0]["result"]["order_id"], "rh-order-1")


if __name__ == "__main__":
    unittest.main()
