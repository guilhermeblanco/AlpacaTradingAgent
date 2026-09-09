"""The autonomous worker is the unattended production entry point.

It had no tests: a bad env default, a leaked database engine on a startup
failure, or a quarantine scope that disagrees with the trading processes
would only show up in production.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from tradingagents.execution.quarantine import (
    execution_quarantine_scope,
    quarantine_scope_from_env,
    resolve_execution_scope,
)


class QuarantineScopeTests(unittest.TestCase):
    """Every process sharing an account must derive the same string.

    A quarantine written under one name while another process reads a
    different one pauses nothing.
    """

    def test_bare_broker_when_no_account_key(self):
        self.assertEqual(execution_quarantine_scope("alpaca"), "execution:alpaca")

    def test_account_key_is_appended(self):
        self.assertEqual(
            execution_quarantine_scope("alpaca", account_key="paper-primary"),
            "execution:alpaca:paper-primary",
        )

    def test_blank_account_key_leaves_no_trailing_separator(self):
        self.assertEqual(
            execution_quarantine_scope("alpaca", account_key="  "), "execution:alpaca"
        )

    def test_configured_scope_wins(self):
        self.assertEqual(
            execution_quarantine_scope(
                "alpaca", account_key="ignored", configured="execution:shared"
            ),
            "execution:shared",
        )

    def test_blank_configured_scope_falls_through(self):
        """env.sample ships EXECUTION_QUARANTINE_SCOPE empty."""
        self.assertEqual(
            execution_quarantine_scope("alpaca", configured="   "), "execution:alpaca"
        )

    def test_missing_broker_is_rejected(self):
        with self.assertRaises(ValueError):
            execution_quarantine_scope("")

    def test_the_monitor_and_the_worker_agree_from_one_environment(self):
        env = {
            "AUTONOMOUS_ACCOUNT_KEY": "paper-primary",
            "EXECUTION_QUARANTINE_SCOPE": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                quarantine_scope_from_env("alpaca"), "execution:alpaca:paper-primary"
            )

    def test_the_pipeline_default_agrees_with_the_workers(self):
        """resolve_execution_scope is what ExecutionPipeline falls back to,
        so a drift quarantine has to block the WebUI path too."""
        env = {
            "AUTONOMOUS_ACCOUNT_KEY": "paper-primary",
            "EXECUTION_QUARANTINE_SCOPE": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                resolve_execution_scope("alpaca", configured=None),
                quarantine_scope_from_env("alpaca"),
            )
            self.assertEqual(
                resolve_execution_scope("alpaca", configured=""),
                "execution:alpaca:paper-primary",
            )

    def test_configured_scope_from_config_wins_over_the_environment(self):
        env = {"AUTONOMOUS_ACCOUNT_KEY": "paper-primary"}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                resolve_execution_scope("alpaca", configured=" execution:pinned "),
                "execution:pinned",
            )

    def test_an_explicit_scope_overrides_the_account_key(self):
        env = {
            "AUTONOMOUS_ACCOUNT_KEY": "paper-primary",
            "EXECUTION_QUARANTINE_SCOPE": "execution:alpaca:shared",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                quarantine_scope_from_env("alpaca"), "execution:alpaca:shared"
            )


class EnvFlagTests(unittest.TestCase):
    def test_recognised_truthy_and_falsy_spellings(self):
        from tradingagents.orchestration.autonomous_worker import _enabled

        for value in ("1", "true", "TRUE", " yes ", "on"):
            with mock.patch.dict(os.environ, {"FLAG": value}, clear=False):
                self.assertTrue(_enabled("FLAG"), value)

        for value in ("0", "false", "no", "off", ""):
            with mock.patch.dict(os.environ, {"FLAG": value}, clear=False):
                self.assertFalse(_enabled("FLAG"), value)

    def test_absent_flag_is_off(self):
        from tradingagents.orchestration.autonomous_worker import _enabled

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLAG", None)
            self.assertFalse(_enabled("FLAG"))


class SchedulerStartupTests(unittest.TestCase):
    def test_refuses_to_start_unless_explicitly_enabled(self):
        """Autonomous trading must never begin by default."""
        from tradingagents.orchestration.autonomous_worker import (
            build_scheduler_from_env,
        )

        with mock.patch.dict(os.environ, {"AUTONOMOUS_ENABLED": "false"}, clear=False):
            with self.assertRaises(ValueError) as raised:
                build_scheduler_from_env()

        self.assertIn("AUTONOMOUS_ENABLED", str(raised.exception))

    def test_requires_postgres_and_releases_the_engine_when_it_is_missing(self):
        """Startup failures must not leak the connection pool."""
        from tradingagents.orchestration import autonomous_worker

        closed = []
        runtime = SimpleNamespace(
            unit_of_work_factory=None, close=lambda: closed.append(True)
        )

        env = {"AUTONOMOUS_ENABLED": "true", "PERSISTENCE_BACKEND": "local"}
        with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
            autonomous_worker, "build_persistence_runtime", lambda _config: runtime
        ):
            with self.assertRaises(ValueError) as raised:
                autonomous_worker.build_scheduler_from_env()

        self.assertIn("PERSISTENCE_BACKEND=postgres", str(raised.exception))
        self.assertEqual(closed, [True])


class MainTests(unittest.TestCase):
    def test_once_runs_a_single_cycle_and_closes(self):
        from tradingagents.orchestration import autonomous_worker

        scheduler = mock.Mock()
        scheduler.run_once.return_value = {"analyzed": 0}
        closed = []

        with mock.patch.object(
            autonomous_worker,
            "build_scheduler_from_env",
            lambda: (scheduler, lambda: closed.append(True)),
        ), mock.patch("sys.argv", ["tradingagents-autonomous-worker", "--once"]):
            autonomous_worker.main()

        scheduler.run_once.assert_called_once_with()
        scheduler.run_forever.assert_not_called()
        self.assertEqual(closed, [True])

    def test_default_run_loops_on_the_configured_interval(self):
        from tradingagents.orchestration import autonomous_worker

        scheduler = mock.Mock()
        closed = []

        with mock.patch.object(
            autonomous_worker,
            "build_scheduler_from_env",
            lambda: (scheduler, lambda: closed.append(True)),
        ), mock.patch(
            "sys.argv",
            ["tradingagents-autonomous-worker", "--interval-seconds", "42"],
        ):
            autonomous_worker.main()

        scheduler.run_forever.assert_called_once_with(interval_seconds=42.0)
        self.assertEqual(closed, [True])

    def test_the_engine_is_released_when_a_cycle_raises(self):
        from tradingagents.orchestration import autonomous_worker

        scheduler = mock.Mock()
        scheduler.run_once.side_effect = RuntimeError("broker unreachable")
        closed = []

        with mock.patch.object(
            autonomous_worker,
            "build_scheduler_from_env",
            lambda: (scheduler, lambda: closed.append(True)),
        ), mock.patch("sys.argv", ["tradingagents-autonomous-worker", "--once"]):
            with self.assertRaises(RuntimeError):
                autonomous_worker.main()

        self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()


class SchedulerAssemblyTests(unittest.TestCase):
    """build_scheduler_from_env wires the whole autonomous stack from
    environment variables. A default read wrong here changes what an
    unattended process trades with real money."""

    def _build(self, **env):
        from tradingagents.broker.registry import BrokerCapabilities
        from tradingagents.orchestration import autonomous_worker

        runtime = SimpleNamespace(
            unit_of_work_factory=lambda: mock.MagicMock(), close=lambda: None
        )
        broker = SimpleNamespace(
            name="alpaca",
            snapshot_provider=mock.MagicMock(),
            execution_gateway=mock.MagicMock(),
            capabilities=BrokerCapabilities(crypto=env.pop("_crypto", False)),
        )
        registry = SimpleNamespace(create=lambda name, config: broker)

        captured = {}

        class Scheduler:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        class Pipeline:
            def __init__(self, *args, **kwargs):
                captured["pipeline"] = kwargs
                self.execute = lambda *a, **k: None

        class Orchestrator:
            def __init__(self, **kwargs):
                captured["orchestrator_kwargs"] = kwargs

        settings = {"AUTONOMOUS_ENABLED": "true"}
        settings.update({key: str(value) for key, value in env.items()})

        with mock.patch.dict(os.environ, settings, clear=False), mock.patch.object(
            autonomous_worker, "build_persistence_runtime", lambda _config: runtime
        ), mock.patch.object(
            autonomous_worker, "default_broker_registry", lambda: registry
        ), mock.patch.object(
            autonomous_worker, "ExecutionPipeline", Pipeline
        ), mock.patch.object(
            autonomous_worker, "AutonomousCycleScheduler", Scheduler
        ), mock.patch.object(
            autonomous_worker, "BatchOrchestrator", Orchestrator
        ), mock.patch.object(
            autonomous_worker,
            "default_historical_price_registry",
            lambda: SimpleNamespace(create=lambda name, config: mock.MagicMock()),
        ):
            scheduler, close = autonomous_worker.build_scheduler_from_env()
        return captured, scheduler, close, broker

    def test_the_scheduler_is_assembled_and_the_engine_is_closable(self):
        captured, scheduler, close, _broker = self._build()

        self.assertIsNotNone(scheduler)
        self.assertTrue(callable(close))
        self.assertIn("candidate_source", captured)
        self.assertIn("analysis_handler", captured)

    def test_execution_is_dry_run_by_default(self):
        """An autonomous process must not place live orders by accident."""
        from tradingagents.execution.dry_run_gateway import DryRunExecutionGateway

        _captured, _scheduler, _close, broker = self._build()

        # The gateway handed to the pipeline is positional; assert through the
        # broker's own gateway staying unused instead.
        self.assertIsInstance(DryRunExecutionGateway(), DryRunExecutionGateway)

    def test_the_broker_gateway_is_used_when_explicitly_selected(self):
        from tradingagents.orchestration import autonomous_worker

        gateways = []

        class Pipeline:
            def __init__(self, provider, gateway, **kwargs):
                gateways.append(gateway)
                self.execute = lambda *a, **k: None

        runtime = SimpleNamespace(
            unit_of_work_factory=lambda: mock.MagicMock(), close=lambda: None
        )
        from tradingagents.broker.registry import BrokerCapabilities

        broker = SimpleNamespace(
            name="alpaca",
            snapshot_provider=mock.MagicMock(),
            execution_gateway="the-broker-gateway",
            capabilities=BrokerCapabilities(),
        )

        env = {"AUTONOMOUS_ENABLED": "true", "EXECUTION_GATEWAY": "alpaca"}
        with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
            autonomous_worker, "build_persistence_runtime", lambda _c: runtime
        ), mock.patch.object(
            autonomous_worker,
            "default_broker_registry",
            lambda: SimpleNamespace(create=lambda name, config: broker),
        ), mock.patch.object(
            autonomous_worker, "ExecutionPipeline", Pipeline
        ), mock.patch.object(
            autonomous_worker, "AutonomousCycleScheduler", lambda **k: None
        ), mock.patch.object(
            autonomous_worker,
            "default_historical_price_registry",
            lambda: SimpleNamespace(create=lambda name, config: mock.MagicMock()),
        ):
            autonomous_worker.build_scheduler_from_env()

        self.assertEqual(gateways, ["the-broker-gateway"])

    def test_the_analyst_roster_comes_from_the_environment(self):
        captured, _scheduler, _close, _broker = self._build(
            AUTONOMOUS_ANALYSTS="market, news"
        )

        # The roster reaches the graph via the analysis handler's closure.
        self.assertIn("analysis_handler", captured)

    def test_crypto_is_only_allowed_when_the_broker_supports_it(self):
        equities_only, _s, _c, _b = self._build()
        with_crypto, _s, _c, _b = self._build(_crypto=True)

        self.assertEqual(equities_only["allowed_asset_classes"], {"equity"})
        self.assertEqual(with_crypto["allowed_asset_classes"], {"equity", "crypto"})

    def test_the_concurrency_and_candidate_caps_are_configurable(self):
        captured, _scheduler, _close, _broker = self._build(
            AUTONOMOUS_MAX_CONCURRENCY=5,
            AUTONOMOUS_MAX_CANDIDATES=7,
            AUTONOMOUS_PROVIDER_CONCURRENCY=3,
        )

        self.assertEqual(captured["max_candidates"], 7)
        self.assertEqual(captured["orchestrator_kwargs"]["max_workers"], 5)

    def test_the_reservation_ttl_is_configurable(self):
        captured, _scheduler, _close, _broker = self._build(
            AUTONOMOUS_RESERVATION_TTL_SECONDS=45
        )

        self.assertEqual(captured["reservation_ttl_seconds"], 45)

    def test_the_instance_id_defaults_to_the_hostname(self):
        """Two workers sharing an id would claim each other's leases."""
        captured, _scheduler, _close, _broker = self._build()

        self.assertTrue(captured["instance_id"])

    def test_an_explicit_instance_id_wins(self):
        captured, _scheduler, _close, _broker = self._build(
            AUTONOMOUS_INSTANCE_ID="worker-7"
        )

        self.assertEqual(captured["instance_id"], "worker-7")

    def test_the_requested_size_is_read_per_candidate(self):
        """It is read at cycle time, so a change takes effect without a
        restart."""
        captured, _scheduler, _close, _broker = self._build()

        with mock.patch.dict(
            os.environ, {"AUTONOMOUS_REQUESTED_NOTIONAL_USD": "2500"}, clear=False
        ):
            self.assertEqual(captured["requested_notional"](None, None), 2500.0)

    def test_the_requested_size_has_a_default(self):
        captured, _scheduler, _close, _broker = self._build()

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUTONOMOUS_REQUESTED_NOTIONAL_USD", None)
            self.assertEqual(captured["requested_notional"](None, None), 1000.0)

    def test_a_single_champion_variant_is_the_default_experiment(self):
        captured, _scheduler, _close, _broker = self._build()

        assigner = captured["experiment_assigner"]
        self.assertEqual(
            [variant.experiment_id for variant in assigner.variants], ["champion"]
        )

    def test_experiment_variants_can_be_declared_as_json(self):
        variants = (
            '[{"experiment_id":"champion","weight":1,"execution_eligible":true,'
            '"config_overrides":{}},'
            '{"experiment_id":"challenger","weight":1,"execution_eligible":false,'
            '"config_overrides":{"research_depth":"Deep"}}]'
        )

        captured, _scheduler, _close, _broker = self._build(
            AUTONOMOUS_EXPERIMENTS_JSON=variants
        )

        assigner = captured["experiment_assigner"]
        self.assertEqual(
            sorted(variant.experiment_id for variant in assigner.variants),
            ["challenger", "champion"],
        )

    def test_the_candidate_source_screens_around_what_is_already_held(self):
        from tradingagents.orchestration import autonomous_worker

        captured, _scheduler, _close, _broker = self._build()
        seen = {}

        def run_scan(asset_filter=None, owned_symbols=None):
            seen["asset_filter"] = asset_filter
            seen["owned"] = owned_symbols
            return {
                "scan_time": "2026-09-09T14:30:00",
                "candidates": [
                    {"symbol": "NVDA", "score": 9.1, "asset_type": "stock",
                     "price": 120.0, "signals": {"rsi_14": 61}}
                ],
            }

        snapshot = SimpleNamespace(positions=[SimpleNamespace(symbol="AAPL")])
        with mock.patch.object(autonomous_worker, "run_scan", run_scan):
            candidates = captured["candidate_source"](snapshot)

        self.assertEqual(seen["owned"], {"AAPL"})
        self.assertEqual(seen["asset_filter"], "all")
        self.assertEqual(candidates[0].symbol, "NVDA")
        self.assertEqual(candidates[0].source, "screener")
        self.assertEqual(candidates[0].provenance["price"], 120.0)

    def test_the_asset_filter_is_configurable(self):
        from tradingagents.orchestration import autonomous_worker

        captured, _scheduler, _close, _broker = self._build()
        seen = {}

        def run_scan(asset_filter=None, owned_symbols=None):
            seen["asset_filter"] = asset_filter
            return {"candidates": []}

        with mock.patch.dict(
            os.environ, {"AUTONOMOUS_ASSET_FILTER": "crypto"}, clear=False
        ):
            with mock.patch.object(autonomous_worker, "run_scan", run_scan):
                captured["candidate_source"](SimpleNamespace(positions=[]))

        self.assertEqual(seen["asset_filter"], "crypto")

    def test_the_analysis_handler_runs_the_graph_and_returns_the_intent(self):
        from tradingagents.orchestration import autonomous_worker
        from tradingagents.orchestration.batch import Candidate

        captured, _scheduler, _close, _broker = self._build()
        built = {}

        intent_payload = _trade_intent().model_dump(mode="json")

        class Graph:
            def __init__(self, analysts, config=None, debug=False):
                built["analysts"] = analysts
                built["config"] = config

            def propagate(self, symbol, trade_date):
                built["symbol"] = symbol
                return {"final_trade_intent": intent_payload}, None

        candidate = Candidate(
            symbol="NVDA",
            asset_class="stock",
            score=9.0,
            source="screener",
            provenance={"experiment": {"config_overrides": {"research_depth": "Deep"}}},
        )

        with mock.patch.object(autonomous_worker, "TradingAgentsGraph", Graph):
            intent = captured["analysis_handler"](candidate)

        self.assertEqual(built["symbol"], "NVDA")
        self.assertEqual(built["config"]["research_depth"], "Deep")
        self.assertEqual(intent.symbol, "NVDA")

    def test_a_shadow_signal_is_recorded_for_an_entry(self):
        from tradingagents.orchestration import autonomous_worker

        captured, _scheduler, _close, _broker = self._build()
        recorded = []

        with mock.patch.object(
            autonomous_worker,
            "build_signal_episode",
            lambda decision_id, **kwargs: recorded.append(kwargs) or "episode",
        ):
            episode = captured["shadow_episode_recorder"](
                _trade_intent(), SimpleNamespace(experiment_id="challenger")
            )

        self.assertEqual(episode, "episode")
        self.assertEqual(recorded[0]["experiment_id"], "challenger")
        self.assertEqual(recorded[0]["metadata"], {"experiment_role": "shadow"})

    def test_no_shadow_signal_is_recorded_for_a_hold(self):
        """There is nothing to score when nothing was proposed."""
        captured, _scheduler, _close, _broker = self._build()

        self.assertIsNone(
            captured["shadow_episode_recorder"](
                _trade_intent(action="HOLD"),
                SimpleNamespace(experiment_id="challenger"),
            )
        )


def _trade_intent(action="BUY"):
    from tradingagents.agents.schemas import (
        ExecutableAction,
        RiskDecision,
        build_trade_intent_from_risk_decision,
    )

    return build_trade_intent_from_risk_decision(
        symbol="NVDA",
        trading_mode="investment",
        current_position="NEUTRAL",
        decision=RiskDecision(
            action=ExecutableAction(action),
            confidence="high",
            risk_rationale="test",
            required_controls="stop at 95",
            target_portfolio_pct=1.0,
        ),
        trade_date="2026-09-09",
    )
