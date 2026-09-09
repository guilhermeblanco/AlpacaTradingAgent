"""Tests for how the pipeline assembles itself when nothing is injected.

`execute_autonomous_trade` is the WebUI's entry point: it fills in the
broker, gateway, journal, persistence, lifecycle store, and risk sizer from
configuration. Two of those defaults matter more than the rest — the
dry-run gateway, which decides whether an order reaches a real broker, and
the quarantine scope, which has to match the one the account monitor
pauses.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from tradingagents.agents.schemas import (
    ExecutableAction,
    RiskDecision,
    build_trade_intent_from_risk_decision,
)
from tradingagents.execution import pipeline as pipeline_module
from tradingagents.execution.pipeline import execute_autonomous_trade


def _intent():
    return build_trade_intent_from_risk_decision(
        symbol="NVDA",
        trading_mode="investment",
        current_position="NEUTRAL",
        decision=RiskDecision(
            action=ExecutableAction.BUY,
            confidence="high",
            risk_rationale="test",
            required_controls="stop at 95",
            target_portfolio_pct=1.0,
        ),
        trade_date="2026-09-09",
    )


class AssemblyFixture(unittest.TestCase):
    def _execute(self, config=None, *, persistence=None, **kwargs):
        captured = {}

        class Pipeline:
            def __init__(self, snapshot_provider, gateway, **options):
                captured["snapshot_provider"] = snapshot_provider
                captured["gateway"] = gateway
                captured.update(options)

            def execute(self, symbol, intent, notional, run_id=None):
                captured["executed"] = (symbol, notional, run_id)
                return {"success": True}

        broker = SimpleNamespace(
            snapshot_provider="broker-provider",
            execution_gateway="broker-gateway",
            capabilities="broker-capabilities",
        )
        runtime = persistence or SimpleNamespace(
            unit_of_work_factory="uow-factory", close=lambda: None
        )

        with mock.patch.object(pipeline_module, "ExecutionPipeline", Pipeline), \
            mock.patch(
                "tradingagents.dataflows.config.get_config", lambda: config or {}
            ), \
            mock.patch(
                "tradingagents.broker.registry.default_broker_registry",
                lambda: SimpleNamespace(create=lambda name, cfg: broker),
            ), \
            mock.patch(
                "tradingagents.persistence.build_persistence_runtime",
                lambda cfg: runtime,
            ):
            result = execute_autonomous_trade(
                "NVDA", _intent(), 1_000.0, **kwargs
            )
        return result, captured


class BrokerDefaultTests(AssemblyFixture):
    def test_the_configured_broker_supplies_both_halves(self):
        _result, captured = self._execute()

        self.assertEqual(captured["snapshot_provider"], "broker-provider")
        self.assertEqual(captured["gateway"], "broker-gateway")
        self.assertEqual(captured["broker_capabilities"], "broker-capabilities")

    def test_dry_run_replaces_the_broker_gateway(self):
        """This is the switch between a simulated fill and a real order."""
        from tradingagents.execution.dry_run_gateway import DryRunExecutionGateway

        _result, captured = self._execute({"execution_gateway": "dry-run"})

        self.assertIsInstance(captured["gateway"], DryRunExecutionGateway)

    def test_an_injected_gateway_is_used_as_given(self):
        """The dry-run setting governs the gateway this function resolves;
        it used to also swap out an injected one, but only when a snapshot
        provider happened not to be injected alongside it."""
        _result, captured = self._execute(
            {"execution_gateway": "dry-run"}, gateway="injected"
        )

        self.assertEqual(captured["gateway"], "injected")

    def test_an_injected_gateway_is_used_with_an_injected_provider_too(self):
        _result, captured = self._execute(
            {"execution_gateway": "dry-run"},
            gateway="injected",
            snapshot_provider="also-injected",
        )

        self.assertEqual(captured["gateway"], "injected")

    def test_the_quarantine_scope_matches_the_account_monitor(self):
        from tradingagents.execution.quarantine import resolve_execution_scope

        _result, captured = self._execute({"execution_broker": "tradier"})

        self.assertEqual(
            captured["execution_control_service"], resolve_execution_scope("tradier")
        )

    def test_a_configured_scope_wins(self):
        _result, captured = self._execute(
            {"execution_quarantine_scope": "execution:custom"}
        )

        self.assertEqual(captured["execution_control_service"], "execution:custom")

    def test_an_injected_scope_is_not_derived(self):
        _result, captured = self._execute(execution_control_service="execution:given")

        self.assertEqual(captured["execution_control_service"], "execution:given")


class PersistenceDefaultTests(AssemblyFixture):
    def test_the_unit_of_work_factory_comes_from_configuration(self):
        _result, captured = self._execute()

        self.assertEqual(captured["unit_of_work_factory"], "uow-factory")

    def test_the_engine_is_released_after_the_trade(self):
        closed = []
        runtime = SimpleNamespace(
            unit_of_work_factory="uow-factory", close=lambda: closed.append(True)
        )

        self._execute(persistence=runtime)

        self.assertEqual(closed, [True])

    def test_the_engine_is_released_even_when_the_trade_raises(self):
        closed = []
        runtime = SimpleNamespace(
            unit_of_work_factory="uow", close=lambda: closed.append(True)
        )

        class Exploding:
            def __init__(self, *args, **kwargs):
                pass

            def execute(self, *args, **kwargs):
                raise RuntimeError("broker unreachable")

        with mock.patch.object(pipeline_module, "ExecutionPipeline", Exploding), \
            mock.patch("tradingagents.dataflows.config.get_config", lambda: {}), \
            mock.patch(
                "tradingagents.broker.registry.default_broker_registry",
                lambda: SimpleNamespace(
                    create=lambda name, cfg: SimpleNamespace(
                        snapshot_provider=None,
                        execution_gateway=None,
                        capabilities=None,
                    )
                ),
            ), \
            mock.patch(
                "tradingagents.persistence.build_persistence_runtime",
                lambda cfg: runtime,
            ):
            with self.assertRaises(RuntimeError):
                execute_autonomous_trade("NVDA", _intent(), 1_000.0)

        self.assertEqual(closed, [True])

    def test_an_injected_factory_opens_no_engine(self):
        opened = []
        runtime = SimpleNamespace(
            unit_of_work_factory="unused", close=lambda: opened.append(True)
        )

        _result, captured = self._execute(
            persistence=runtime, unit_of_work_factory="injected"
        )

        self.assertEqual(captured["unit_of_work_factory"], "injected")
        self.assertEqual(opened, [])


class OptionalServiceTests(AssemblyFixture):
    def test_a_journal_is_created_under_the_results_directory(self):
        _result, captured = self._execute({"results_dir": "somewhere"})

        self.assertIsNotNone(captured["journal"])

    def test_risk_sizing_is_off_unless_configured(self):
        _result, captured = self._execute()

        self.assertIsNone(captured["risk_sizer"])

    def test_enabling_risk_sizing_builds_the_service(self):
        with mock.patch(
            "tradingagents.marketdata.get_research_market_data_provider",
            lambda cfg: "provider",
        ):
            _result, captured = self._execute(
                {"risk_sizing_enabled": True, "risk_sizing_params": {}}
            )

        self.assertIsNotNone(captured["risk_sizer"])

    def test_the_lifecycle_ttl_and_switch_come_from_configuration(self):
        _result, captured = self._execute(
            {"lifecycle_enabled": False, "lifecycle_intent_ttl_seconds": 45}
        )

        self.assertFalse(captured["lifecycle_enabled"])
        self.assertEqual(captured["lifecycle_ttl_seconds"], 45)

    def test_the_lifecycle_defaults_are_conservative(self):
        _result, captured = self._execute()

        self.assertTrue(captured["lifecycle_enabled"])
        self.assertEqual(captured["lifecycle_ttl_seconds"], 900)

    def test_an_unreadable_configuration_does_not_stop_the_trade(self):
        captured = {}

        class Pipeline:
            def __init__(self, snapshot_provider, gateway, **options):
                captured.update(options)

            def execute(self, *args, **kwargs):
                return {"success": True}

        with mock.patch.object(pipeline_module, "ExecutionPipeline", Pipeline), \
            mock.patch(
                "tradingagents.dataflows.config.get_config",
                mock.Mock(side_effect=RuntimeError("config unreadable")),
            ), \
            mock.patch(
                "tradingagents.broker.registry.default_broker_registry",
                lambda: SimpleNamespace(
                    create=lambda name, cfg: SimpleNamespace(
                        snapshot_provider="p", execution_gateway="g", capabilities="c"
                    )
                ),
            ), \
            mock.patch(
                "tradingagents.persistence.build_persistence_runtime",
                lambda cfg: SimpleNamespace(
                    unit_of_work_factory=None, close=lambda: None
                ),
            ):
            result = execute_autonomous_trade("NVDA", _intent(), 1_000.0)

        self.assertTrue(result["success"])

    def test_the_run_id_reaches_the_pipeline(self):
        _result, captured = self._execute(run_id="run-7")

        self.assertEqual(captured["executed"], ("NVDA", 1_000.0, "run-7"))


if __name__ == "__main__":
    unittest.main()
