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

    def __init__(self, succeed=True):
        self.submissions = []
        self.succeed = succeed

    def submit(self, intent, *, client_order_id):
        self.submissions.append((intent, client_order_id))
        return OptionsExecutionResult(
            success=self.succeed, decision_id=intent.decision_id,
            underlying=intent.underlying,
            gateway=self.name, intent=intent, broker_order_id="order-1",
            client_order_id=client_order_id,
            status="accepted" if self.succeed else "rejected",
            error=None if self.succeed else "insufficient options level",
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


class RecordingJournal:
    """Captures the audit trail instead of writing it to disk."""

    def __init__(self):
        self.events = []
        self.run_ids = []

    def append(self, event, *, symbol=None, decision_id=None, run_id=None, payload=None):
        self.events.append((event, payload or {}))
        self.run_ids.append((event, decision_id, run_id))


def _valid_intent():
    return long_call()


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


class OptionsPipelineJournalTests(unittest.TestCase):
    """Every options decision leaves a trail, whether it was sent or refused."""

    def _pipeline(self, gateway=None, **policy_overrides):
        from tradingagents.options.pipeline import OptionsExecutionPipeline
        from tradingagents.options.validator import OptionsRiskPolicy

        policy = OptionsRiskPolicy(enabled=True, paper_only=True, **policy_overrides)
        self.journal = RecordingJournal()
        return OptionsExecutionPipeline(
            gateway or FakeGateway(),
            policy=policy,
            journal=self.journal,
            is_paper=True,
        )

    def test_a_malformed_intent_is_refused_before_anything_is_journalled(self):
        pipeline = self._pipeline()

        result = pipeline.execute({"not": "an intent"})

        self.assertFalse(result["success"])
        self.assertIn("Invalid options intent", result["error"])
        self.assertEqual(self.journal.events, [])

    def test_a_valid_intent_is_journalled_on_receipt_and_completion(self):
        pipeline = self._pipeline()

        pipeline.execute(_valid_intent(), run_id="run-1")

        self.assertEqual(
            [name for name, _payload in self.journal.events],
            ["options_intent_received", "options_execution_completed"],
        )

    def test_the_client_order_id_is_derived_from_the_decision(self):
        """Two submissions of one decision must not become two positions."""
        intent = _valid_intent()
        pipeline = self._pipeline()

        pipeline.execute(intent)
        first = self.journal.events[0][1]["client_order_id"]

        pipeline = self._pipeline()
        pipeline.execute(intent)

        self.assertEqual(self.journal.events[0][1]["client_order_id"], first)
        self.assertTrue(first.startswith("ata-opt-"))

    def test_two_different_decisions_get_different_order_ids(self):
        pipeline = self._pipeline()

        pipeline.execute(_valid_intent())
        pipeline.execute(_valid_intent())

        ids = [
            payload["client_order_id"]
            for name, payload in self.journal.events
            if name == "options_intent_received"
        ]
        self.assertEqual(len(set(ids)), 2)

    def test_a_blocked_intent_is_journalled_with_its_reasons(self):
        pipeline = self._pipeline(max_contracts=1)

        result = pipeline.execute(long_call(quantity=5))

        self.assertFalse(result["success"])
        self.assertTrue(result["validations"])
        self.assertIn(
            "options_validation_blocked",
            [name for name, _payload in self.journal.events],
        )

    def test_a_broker_rejection_is_journalled_as_such(self):
        gateway = FakeGateway(succeed=False)
        pipeline = self._pipeline(gateway)

        pipeline.execute(_valid_intent())

        self.assertIn(
            "options_broker_rejected",
            [name for name, _payload in self.journal.events],
        )

    def test_the_run_id_is_carried_onto_every_event(self):
        pipeline = self._pipeline()

        pipeline.execute(_valid_intent(), run_id="run-7")

        self.assertTrue(all(entry[2] == "run-7" for entry in self.journal.run_ids))


class OptionsPolicyWiringTests(unittest.TestCase):
    """The policy is assembled from configuration; a wrong key silently
    disables a guard."""

    def _execute(self, config, gateway=None):
        from unittest import mock

        from tradingagents.options.pipeline import execute_autonomous_options_trade

        captured = {}

        class CapturingGateway(FakeGateway):
            def submit(self, intent, *, client_order_id):
                captured["submitted"] = True
                return super().submit(intent, client_order_id=client_order_id)

        with mock.patch(
            "tradingagents.dataflows.config.get_alpaca_use_paper", lambda: "true"
        ):
            result = execute_autonomous_options_trade(
                _valid_intent(),
                gateway=gateway or CapturingGateway(),
                journal=RecordingJournal(),
                config=config,
            )
        return result, captured

    def test_options_trading_is_off_unless_turned_on(self):
        result, captured = self._execute({})

        self.assertFalse(result["success"])
        self.assertNotIn("submitted", captured)

    def test_enabling_it_lets_a_compliant_intent_through(self):
        result, captured = self._execute({"options_autonomous_enabled": True})

        self.assertTrue(result["success"])
        self.assertTrue(captured["submitted"])

    def test_the_contract_cap_is_read_from_configuration(self):
        from unittest import mock

        from tradingagents.options.pipeline import execute_autonomous_options_trade

        with mock.patch(
            "tradingagents.dataflows.config.get_alpaca_use_paper", lambda: "true"
        ):
            result = execute_autonomous_options_trade(
                long_call(quantity=5),
                gateway=FakeGateway(),
                journal=RecordingJournal(),
                config={"options_autonomous_enabled": True, "options_max_contracts": 1},
            )

        self.assertFalse(result["success"])

    def test_the_configuration_is_read_from_the_process_when_not_supplied(self):
        from unittest import mock

        from tradingagents.options.pipeline import execute_autonomous_options_trade

        with mock.patch(
            "tradingagents.dataflows.config.get_config",
            lambda: {"options_autonomous_enabled": False},
        ):
            with mock.patch(
                "tradingagents.dataflows.config.get_alpaca_use_paper", lambda: "true"
            ):
                result = execute_autonomous_options_trade(
                    _valid_intent(),
                    gateway=FakeGateway(),
                    journal=RecordingJournal(),
                )

        self.assertFalse(result["success"])
