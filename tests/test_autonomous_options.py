import tempfile
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from tradingagents.execution.journal import ExecutionJournal
from tradingagents.options.models import (
    OptionLeg, OptionPositionIntent, OptionsExecutionResult, OptionsTradeIntent,
)
from tradingagents.options.gateway import AlpacaOptionsGateway
from tradingagents.options.occ import format_occ_symbol, parse_occ_symbol
from tradingagents.options.pipeline import OptionsExecutionPipeline
from tradingagents.options.validator import (
    OptionsRiskPolicy, deterministic_max_loss_usd, validate_options_intent,
)


class FakeGateway:
    name = "fake-options"

    def __init__(self):
        self.submissions = []

    def submit(self, intent, *, client_order_id):
        self.submissions.append((intent, client_order_id))
        return OptionsExecutionResult(
            success=True, decision_id=intent.decision_id, underlying=intent.underlying,
            gateway=self.name, intent=intent, broker_order_id="order-1",
            client_order_id=client_order_id, status="accepted",
        )


def long_call(**overrides):
    expiration = date.today() + timedelta(days=30)
    values = dict(
        underlying="AAPL", strategy="long_call", quantity=1,
        legs=[OptionLeg(
            symbol=format_occ_symbol("AAPL", expiration, "call", 200),
            position_intent=OptionPositionIntent.BUY_TO_OPEN,
            bid_price=4.9, ask_price=5.1, open_interest=1000, volume=200,
        )],
        limit_price=5.0, max_loss_usd=500, thesis="Defined-risk bullish trade",
    )
    values.update(overrides)
    return OptionsTradeIntent(**values)


class AutonomousOptionsTests(unittest.TestCase):
    def test_occ_round_trip(self):
        symbol = format_occ_symbol("AAPL", "2027-01-15", "call", 200)
        parsed = parse_occ_symbol(symbol)
        self.assertEqual(parsed["underlying"], "AAPL")
        self.assertEqual(parsed["strike"], 200)

    def test_valid_defined_risk_intent_passes(self):
        self.assertEqual(validate_options_intent(long_call(), OptionsRiskPolicy(enabled=True)), [])
        self.assertEqual(deterministic_max_loss_usd(long_call()), 500)

    def test_liquidity_and_live_policy_fail_closed(self):
        intent = long_call()
        intent.legs[0].open_interest = 1
        errors = validate_options_intent(intent, OptionsRiskPolicy(enabled=True), is_paper=False)
        self.assertTrue(any("paper" in error.lower() for error in errors))
        self.assertTrue(any("open interest" in error.lower() for error in errors))

    def test_pipeline_only_submits_valid_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            gateway = FakeGateway()
            pipeline = OptionsExecutionPipeline(
                gateway, policy=OptionsRiskPolicy(enabled=True),
                journal=ExecutionJournal(tmp), is_paper=True,
            )
            result = pipeline.execute(long_call())
            self.assertTrue(result["success"])
            self.assertEqual(len(gateway.submissions), 1)
            self.assertTrue(result["client_order_id"].startswith("ata-opt-"))

    def test_undefined_risk_short_is_blocked(self):
        expiration = date.today() + timedelta(days=30)
        intent = long_call(
            strategy="naked_call",
            legs=[OptionLeg(
                symbol=format_occ_symbol("AAPL", expiration, "call", 250),
                position_intent=OptionPositionIntent.SELL_TO_OPEN,
                bid_price=1.0, ask_price=1.05, open_interest=1000,
            )],
        )
        policy = OptionsRiskPolicy(enabled=True, allowed_strategies={"naked_call"})
        errors = validate_options_intent(intent, policy)
        self.assertTrue(any("Undefined-risk" in error for error in errors))

    def test_alpaca_gateway_builds_native_multileg_order(self):
        expiration = date.today() + timedelta(days=30)
        intent = long_call(
            strategy="debit_spread",
            legs=[
                long_call().legs[0],
                OptionLeg(
                    symbol=format_occ_symbol("AAPL", expiration, "call", 210),
                    position_intent=OptionPositionIntent.SELL_TO_OPEN,
                    bid_price=2.0, ask_price=2.1, open_interest=500,
                ),
            ],
            limit_price=3.0,
        )
        client = SimpleNamespace(submit_order=lambda request: SimpleNamespace(
            id="order-2", client_order_id=request.client_order_id, status="accepted"
        ))
        with patch(
            "tradingagents.dataflows.alpaca_utils.get_alpaca_trading_client",
            return_value=client,
        ):
            result = AlpacaOptionsGateway().submit(intent, client_order_id="ata-opt-test")
        self.assertTrue(result.success)
        self.assertEqual(result.client_order_id, "ata-opt-test")

    def test_rejects_model_understatement_of_spread_max_loss(self):
        expiration = date.today() + timedelta(days=30)
        intent = long_call(
            strategy="credit_spread", limit_price=1.0, max_loss_usd=100,
            legs=[
                OptionLeg(
                    symbol=format_occ_symbol("AAPL", expiration, "call", 200),
                    position_intent=OptionPositionIntent.SELL_TO_OPEN,
                    bid_price=4.9, ask_price=5.1, open_interest=1000,
                ),
                OptionLeg(
                    symbol=format_occ_symbol("AAPL", expiration, "call", 205),
                    position_intent=OptionPositionIntent.BUY_TO_OPEN,
                    bid_price=3.9, ask_price=4.1, open_interest=1000,
                ),
            ],
        )
        self.assertEqual(deterministic_max_loss_usd(intent), 400)
        errors = validate_options_intent(intent, OptionsRiskPolicy(enabled=True))
        self.assertTrue(any("understates" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
