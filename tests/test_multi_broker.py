import unittest

from tradingagents.agents.schemas import ExecutableAction, RiskDecision, build_trade_intent_from_risk_decision
from tradingagents.broker.registry import (
    ALPACA_CAPABILITIES,
    BROKER_CAPABILITY_MATRIX,
    ROBINHOOD_CAPABILITIES,
    TRADIER_CAPABILITIES,
    BrokerCapabilities,
    BrokerRegistry,
    BrokerRuntime,
)
from tradingagents.broker.robinhood import RobinhoodExecutionGateway, RobinhoodSnapshotProvider
from tradingagents.broker.tradier import TradierClient, TradierExecutionGateway, TradierSnapshotProvider
from tradingagents.execution.dry_run_gateway import DryRunExecutionGateway
from tradingagents.execution.gateway import SubmissionUncertain
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
        if path.endswith("/orders/123"):
            return {"order": {"id": 123, "symbol": "AAPL", "side": "buy",
                               "quantity": 10, "status": "filled",
                               "exec_quantity": 10, "avg_fill_price": 100.5,
                               "tag": "ata-d1-0"}}
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
        if name == "get_equity_orders":
            return {"orders": [{"order_id": "rh-order-1", "ref_id": "ata-d1-0",
                                 "symbol": "AAPL", "side": "buy", "status": "filled",
                                 "quantity": "10", "filled_quantity": "10",
                                 "average_fill_price": "100.75"}]}
        raise AssertionError(name)


class TimeoutTradierTransport(FakeTradierTransport):
    def __call__(self, method, path, *, params=None, data=None):
        if method == "POST":
            raise TimeoutError("response timed out")
        return super().__call__(method, path, params=params, data=data)


class TimeoutRobinhoodClient(FakeRobinhoodClient):
    def call_tool(self, name, arguments):
        if name == "place_equity_order":
            raise TimeoutError("response timed out")
        return super().call_tool(name, arguments)


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
    def test_capability_matrix_matches_implemented_adapters(self):
        matrix = BROKER_CAPABILITY_MATRIX

        self.assertTrue(matrix["alpaca"].crypto)
        self.assertTrue(matrix["alpaca"].fractional_equities)
        self.assertTrue(matrix["alpaca"].shorting)
        self.assertTrue(matrix["alpaca"].native_brackets)

        self.assertFalse(matrix["tradier"].crypto)
        self.assertFalse(matrix["tradier"].fractional_equities)
        self.assertTrue(matrix["tradier"].shorting)
        self.assertFalse(matrix["tradier"].options)
        self.assertFalse(matrix["tradier"].native_brackets)

        self.assertFalse(matrix["robinhood"].crypto)
        self.assertTrue(matrix["robinhood"].fractional_equities)
        self.assertFalse(matrix["robinhood"].shorting)
        self.assertFalse(matrix["robinhood"].native_brackets)
        self.assertEqual(matrix["alpaca"].to_dict()["time_in_force"], ["day", "gtc"])

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

    def test_tradier_gateway_reconciles_cumulative_fill(self):
        gateway = TradierExecutionGateway(TradierClient(
            token="token", account_id="account", transport=FakeTradierTransport(),
        ))
        snapshot = gateway.get_order_snapshot(order_id="123")
        self.assertEqual(snapshot.status.value, "filled")
        self.assertEqual(snapshot.filled_quantity, 10)
        self.assertEqual(snapshot.filled_avg_price, 100.5)

    def test_tradier_transport_timeout_is_uncertain(self):
        gateway = TradierExecutionGateway(TradierClient(
            token="token", account_id="account", transport=TimeoutTradierTransport(),
        ))
        with self.assertRaises(SubmissionUncertain) as raised:
            gateway.submit_plan(execution_plan(), trade_intent())
        self.assertEqual(
            raised.exception.actions[0]["result"]["client_order_id"], "ata-d1-0"
        )

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

    def test_robinhood_gateway_reconciles_order_history(self):
        gateway = RobinhoodExecutionGateway(FakeRobinhoodClient())
        snapshot = gateway.get_order_snapshot(client_order_id="ata-d1-0")
        self.assertEqual(snapshot.order_id, "rh-order-1")
        self.assertEqual(snapshot.status.value, "filled")
        self.assertEqual(snapshot.filled_quantity, 10)
        self.assertEqual(snapshot.filled_avg_price, 100.75)

    def test_robinhood_transport_timeout_is_uncertain(self):
        gateway = RobinhoodExecutionGateway(
            TimeoutRobinhoodClient(), review_only=False, live_orders_enabled=True
        )
        with self.assertRaises(SubmissionUncertain) as raised:
            gateway.submit_plan(execution_plan(), trade_intent())
        self.assertEqual(
            raised.exception.actions[0]["result"]["client_order_id"], "ata-d1-0"
        )


if __name__ == "__main__":
    unittest.main()


class ScriptedTradier:
    """Answers each Tradier path from a supplied table."""

    def __init__(self, responses=None, error=None):
        self.responses = responses or {}
        self.error = error
        self.calls = []

    def __call__(self, method, path, *, params=None, data=None):
        self.calls.append((method, path, params, data))
        if self.error:
            raise self.error
        for suffix, payload in self.responses.items():
            if path.endswith(suffix):
                return payload() if callable(payload) else payload
        return {}


def _tradier(responses=None, error=None):
    transport = ScriptedTradier(responses, error)
    return (
        TradierClient(token="t", account_id="a", transport=transport),
        transport,
    )


class TradierClientTests(unittest.TestCase):
    def test_both_halves_of_the_credential_are_required(self):
        for token, account in (("", "a"), ("t", "")):
            with self.assertRaises(ValueError):
                TradierClient(token=token, account_id=account)

    def test_sandbox_and_live_use_different_hosts(self):
        sandbox = TradierClient(token="t", account_id="a", sandbox=True)
        live = TradierClient(token="t", account_id="a", sandbox=False)

        self.assertIn("sandbox.tradier.com", sandbox.base_url)
        self.assertNotIn("sandbox", live.base_url)

    def test_a_request_reaches_the_transport_with_its_parameters(self):
        client, transport = _tradier()

        client.request("GET", "/markets/quotes", params={"symbols": "AAPL"})

        self.assertEqual(
            transport.calls[0][:3], ("GET", "/markets/quotes", {"symbols": "AAPL"})
        )


class TradierQuoteTests(unittest.TestCase):
    def _quote(self, payload):
        client, _transport = _tradier({"/markets/quotes": payload})
        return TradierSnapshotProvider(client).get_quote_snapshot("AAPL")

    def test_a_quote_is_normalized(self):
        quote = self._quote({"quotes": {"quote": {"bid": 99, "ask": 101, "last": 100}}})

        self.assertEqual((quote.bid_price, quote.ask_price, quote.last_price), (99, 101, 100))

    def test_a_list_of_quotes_takes_the_first(self):
        quote = self._quote(
            {"quotes": {"quote": [{"bid": 99, "ask": 101, "last": 100}, {"bid": 1}]}}
        )

        self.assertEqual(quote.bid_price, 99)

    def test_the_previous_close_stands_in_for_a_missing_last_trade(self):
        quote = self._quote({"quotes": {"quote": {"bid": 99, "ask": 101, "close": 98}}})

        self.assertEqual(quote.last_price, 98)

    def test_an_empty_quote_reads_as_unknown_not_zero(self):
        quote = self._quote({"quotes": {}})

        self.assertIsNone(quote.bid_price)
        self.assertIsNone(quote.last_price)


class TradierClosePositionTests(unittest.TestCase):
    def _close(self, position=None, order=None):
        responses = {
            "/balances": {"balances": {"total_equity": 100000, "total_cash": 50000}},
            "/positions": {"positions": {"position": position} if position else {}},
            "/orders": order if order is not None else {"order": {"id": 9}},
        }
        client, transport = _tradier(responses)
        return TradierExecutionGateway(client).close_position("AAPL"), transport

    def test_a_long_position_is_sold(self):
        result, transport = self._close(
            {"symbol": "AAPL", "quantity": 10, "cost_basis": 900}
        )

        order = next(data for _m, path, _p, data in transport.calls if path.endswith("/orders"))
        self.assertEqual(order["side"], "sell")
        self.assertEqual(order["quantity"], 10)
        self.assertTrue(result["success"])

    def test_a_short_position_is_covered(self):
        _result, transport = self._close(
            {"symbol": "AAPL", "quantity": -10, "cost_basis": 900}
        )

        order = next(data for _m, path, _p, data in transport.calls if path.endswith("/orders"))
        self.assertEqual(order["side"], "buy_to_cover")
        self.assertEqual(order["quantity"], 10)

    def test_closing_nothing_sends_no_order(self):
        result, transport = self._close(position=None)

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "already_closed")
        self.assertFalse(
            [call for call in transport.calls if call[0] == "POST"]
        )

    def test_a_refused_close_is_reported(self):
        result, _transport = self._close(
            {"symbol": "AAPL", "quantity": 10, "cost_basis": 900}, order={"order": {}}
        )

        self.assertFalse(result["success"])


class TradierOrderSnapshotTests(unittest.TestCase):
    ORDER = {
        "id": 123,
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 10,
        "status": "filled",
        "exec_quantity": 10,
        "avg_fill_price": 100.5,
        "tag": "ata-d1-0",
    }

    def _snapshot(self, responses, **kwargs):
        client, transport = _tradier(responses)
        return TradierExecutionGateway(client).get_order_snapshot(**kwargs), transport

    def test_asking_for_neither_identifier_is_refused(self):
        client, _transport = _tradier()

        with self.assertRaises(ValueError):
            TradierExecutionGateway(client).get_order_snapshot()

    def test_a_tag_is_matched_against_the_order_list(self):
        """The tag is our idempotency key; Tradier has no client order id."""
        snapshot, _transport = self._snapshot(
            {"/orders": {"orders": {"order": [self.ORDER]}}},
            client_order_id="ata-d1-0",
        )

        self.assertEqual(snapshot.order_id, "123")
        self.assertEqual(snapshot.client_order_id, "ata-d1-0")

    def test_an_unknown_tag_is_reported_rather_than_guessed(self):
        with self.assertRaises(KeyError):
            self._snapshot(
                {"/orders": {"orders": {"order": []}}}, client_order_id="missing"
            )

    def test_an_empty_order_list_is_reported(self):
        with self.assertRaises(KeyError):
            self._snapshot({"/orders": {"orders": "null"}}, client_order_id="x")

    def test_a_response_with_no_order_is_refused(self):
        with self.assertRaises(RuntimeError):
            self._snapshot({"/orders/123": {"order": {}}}, order_id=123)

    def test_each_status_is_mapped(self):
        from tradingagents.execution.reconciliation import BrokerOrderStatus

        cases = {
            "pending": BrokerOrderStatus.NEW,
            "open": BrokerOrderStatus.NEW,
            "partially_filled": BrokerOrderStatus.PARTIALLY_FILLED,
            "filled": BrokerOrderStatus.FILLED,
            "canceled": BrokerOrderStatus.CANCELED,
            "cancelled": BrokerOrderStatus.CANCELED,
            "rejected": BrokerOrderStatus.REJECTED,
            "error": BrokerOrderStatus.REJECTED,
            "expired": BrokerOrderStatus.EXPIRED,
            "something-new": BrokerOrderStatus.UNKNOWN,
        }

        for raw, expected in cases.items():
            snapshot, _transport = self._snapshot(
                {"/orders/123": {"order": {**self.ORDER, "status": raw}}},
                order_id=123,
            )

            self.assertEqual(snapshot.status, expected, raw)

    def test_a_short_cover_reads_as_a_buy(self):
        snapshot, _transport = self._snapshot(
            {"/orders/123": {"order": {**self.ORDER, "side": "buy_to_cover"}}},
            order_id=123,
        )

        self.assertEqual(snapshot.side, "buy")

    def test_a_short_sale_reads_as_a_sell(self):
        snapshot, _transport = self._snapshot(
            {"/orders/123": {"order": {**self.ORDER, "side": "sell_short"}}},
            order_id=123,
        )

        self.assertEqual(snapshot.side, "sell")

    def test_the_alternate_fill_quantity_field_is_read(self):
        snapshot, _transport = self._snapshot(
            {
                "/orders/123": {
                    "order": {
                        **self.ORDER,
                        "exec_quantity": None,
                        "executed_quantity": 4,
                    }
                }
            },
            order_id=123,
        )

        self.assertEqual(snapshot.filled_quantity, 4.0)

    def test_an_unfilled_order_has_no_fill_price(self):
        snapshot, _transport = self._snapshot(
            {
                "/orders/123": {
                    "order": {
                        **self.ORDER,
                        "status": "open",
                        "exec_quantity": 0,
                        "avg_fill_price": 0,
                    }
                }
            },
            order_id=123,
        )

        self.assertIsNone(snapshot.filled_avg_price)

    def test_a_notional_order_has_no_requested_quantity(self):
        snapshot, _transport = self._snapshot(
            {"/orders/123": {"order": {**self.ORDER, "quantity": None}}},
            order_id=123,
        )

        self.assertIsNone(snapshot.requested_quantity)

    def test_multileg_orders_carry_their_legs(self):
        from tradingagents.execution.reconciliation import BrokerOrderStatus

        snapshot, _transport = self._snapshot(
            {
                "/orders/123": {
                    "order": {
                        **self.ORDER,
                        "leg": [
                            {
                                "id": 1,
                                "symbol": "AAPL",
                                "side": "buy_to_open",
                                "quantity": 5,
                                "status": "filled",
                                "exec_quantity": 5,
                                "avg_fill_price": 100.0,
                            },
                            {
                                "id": 2,
                                "option_symbol": "AAPL260116C00200000",
                                "side": "sell_to_open",
                                "quantity": 5,
                                "status": "no-such-status",
                            },
                        ],
                    }
                }
            },
            order_id=123,
        )

        first, second = snapshot.child_orders
        self.assertEqual((first.side, first.status), ("buy", BrokerOrderStatus.FILLED))
        self.assertEqual(second.symbol, "AAPL260116C00200000")
        self.assertEqual(second.side, "sell")
        self.assertEqual(second.status, BrokerOrderStatus.UNKNOWN)
        self.assertEqual(second.filled_quantity, 0.0)
        self.assertIsNone(second.filled_avg_price)
