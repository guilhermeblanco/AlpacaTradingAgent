"""Wiring tests for the safety guardrails WebUI panel and callbacks."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tradingagents.safety import DEFAULT_SAFETY_CONFIG, SafetyGuard


def _guard(tmp, **overrides):
    config = dict(DEFAULT_SAFETY_CONFIG)
    config.update(overrides)
    return SafetyGuard(
        config=config,
        state_path=Path(tmp) / "state.json",
        kill_switch_path=Path(tmp) / "KILL_SWITCH",
    )


class SafetyPanelWiringTests(unittest.TestCase):
    def test_panel_component_builds(self):
        from webui.components.safety_panel import create_safety_panel

        rendered = str(create_safety_panel())
        for component_id in (
            "safety-status-container",
            "safety-kill-switch-btn",
            "safety-release-btn",
            "safety-refresh-interval",
            "safety-action-status",
        ):
            self.assertIn(component_id, rendered)

    def test_callbacks_register_on_fresh_app(self):
        import dash

        from webui.callbacks.safety_callbacks import register_safety_callbacks

        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        register_safety_callbacks(app)
        self.assertTrue(
            any("safety-status-container" in key for key in app.callback_map),
            f"safety callback missing from callback map: {list(app.callback_map)}",
        )

    def test_status_cards_render_green_and_red(self):
        from webui.callbacks.safety_callbacks import _status_cards

        with tempfile.TemporaryDirectory() as tmp:
            guard = _guard(tmp)
            healthy = str(
                _status_cards(
                    guard.status(account={"equity": 100_000.0, "last_equity": 100_000.0})
                )
            )
            self.assertIn("Kill Switch", healthy)
            self.assertIn("LLM Budget", healthy)
            self.assertIn("fa-check-circle", healthy)

            guard.engage_kill_switch("test halt")
            tripped = str(
                _status_cards(
                    guard.status(account={"equity": 100_000.0, "last_equity": 100_000.0})
                )
            )
            self.assertIn("fa-exclamation-triangle", tripped)
            self.assertIn("test halt", tripped)

    def test_analysis_start_is_gated_by_llm_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = _guard(tmp, daily_llm_token_budget=100)
            guard.record_llm_tokens(500)
            with patch("tradingagents.safety.get_safety_guard", return_value=guard):
                from webui.components.analysis import start_analysis

                message = start_analysis(
                    ticker="AAPL",
                    analysts_market=True,
                    analysts_social=False,
                    analysts_news=False,
                    analysts_fundamentals=False,
                    analysts_macro=False,
                    research_depth="Shallow",
                    allow_shorts=False,
                    quick_llm="gpt-test",
                    deep_llm="gpt-test",
                )
            self.assertIsInstance(message, str)
            self.assertIn("budget", message.lower())


if __name__ == "__main__":
    unittest.main()


class SafetyLimitsEditorTests(unittest.TestCase):
    """The deterministic limits were backend-only; the panel now edits them."""

    def setUp(self):
        from tradingagents.dataflows.config import get_config, set_config

        self._original = {
            key: (get_config() or {}).get(key)
            for key in (
                "execution_gateway",
                "safety_enabled",
                "max_trade_notional_usd",
                "max_symbol_concentration_pct",
                "daily_loss_halt_pct",
                "max_drawdown_halt_pct",
                "max_consecutive_rejections",
                "daily_llm_token_budget",
            )
        }
        self.addCleanup(lambda: set_config(self._original))

    def test_panel_exposes_every_editable_limit(self):
        from webui.components.safety_panel import (
            SAFETY_LIMIT_FIELDS,
            create_safety_panel,
        )

        rendered = str(create_safety_panel())
        self.assertIn("safety-execution-gateway", rendered)
        self.assertIn("safety-enabled-switch", rendered)
        self.assertIn("safety-limits-save", rendered)
        for input_id, _key, _label, _help, _step in SAFETY_LIMIT_FIELDS:
            self.assertIn(input_id, rendered)

    def test_saving_limits_updates_config_and_reloads_the_guard(self):
        from tradingagents.dataflows.config import get_config
        from tradingagents.safety import get_safety_guard
        from webui.callbacks.safety_callbacks import apply_safety_limits

        get_safety_guard()  # build the singleton so the reset is observable

        saved, message = apply_safety_limits(
            "alpaca", True, [1234, 10, 5, 7, 3, 50_000]
        )

        self.assertTrue(saved, message)
        config = get_config()
        self.assertEqual(config["max_trade_notional_usd"], 1234)
        self.assertEqual(config["max_symbol_concentration_pct"], 10)
        self.assertEqual(config["daily_llm_token_budget"], 50_000)
        self.assertEqual(get_safety_guard().config["max_trade_notional_usd"], 1234)

    def test_dry_run_gateway_is_selectable_and_called_out(self):
        from tradingagents.dataflows.config import get_config
        from webui.callbacks.safety_callbacks import apply_safety_limits

        saved, message = apply_safety_limits(
            "dry-run", True, [1000, 10, 5, 7, 3, 0]
        )

        self.assertTrue(saved, message)
        self.assertEqual(get_config()["execution_gateway"], "dry-run")
        self.assertIn("no orders are sent", message)

    def test_disabling_the_safety_layer_says_so(self):
        from webui.callbacks.safety_callbacks import apply_safety_limits

        saved, message = apply_safety_limits(
            "alpaca", False, [1000, 10, 5, 7, 3, 0]
        )

        self.assertTrue(saved, message)
        self.assertIn("DISABLED", message)

    def test_unknown_gateway_is_rejected_without_changing_config(self):
        from tradingagents.dataflows.config import get_config
        from webui.callbacks.safety_callbacks import apply_safety_limits

        before = get_config()["execution_gateway"]
        saved, message = apply_safety_limits(
            "not-a-gateway", True, [1000, 10, 5, 7, 3, 0]
        )

        self.assertFalse(saved)
        self.assertIn("not saved", message)
        self.assertEqual(get_config()["execution_gateway"], before)

    def test_blank_input_keeps_the_stored_limit(self):
        from tradingagents.dataflows.config import get_config
        from webui.callbacks.safety_callbacks import apply_safety_limits

        apply_safety_limits("alpaca", True, [4321, 10, 5, 7, 3, 0])
        saved, message = apply_safety_limits("alpaca", True, ["", 10, 5, 7, 3, 0])

        self.assertTrue(saved, message)
        self.assertEqual(get_config()["max_trade_notional_usd"], 4321)

    def test_reading_limits_matches_the_field_order(self):
        from webui.callbacks.safety_callbacks import (
            SAFETY_LIMIT_KEYS,
            apply_safety_limits,
            read_safety_limits,
        )

        apply_safety_limits("dry-run", False, [11, 22, 33, 44, 55, 66])
        gateway, enabled, values = read_safety_limits()

        self.assertEqual(gateway, "dry-run")
        self.assertFalse(enabled)
        self.assertEqual(values, [11, 22, 33, 44, 55, 66])
        self.assertEqual(len(values), len(SAFETY_LIMIT_KEYS))
