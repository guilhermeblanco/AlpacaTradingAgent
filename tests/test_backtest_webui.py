"""Wiring tests for the backtest WebUI panel and callbacks."""

import unittest

import pandas as pd


class BacktestPanelWiringTests(unittest.TestCase):
    def test_panel_component_builds(self):
        from webui.components.backtest_panel import create_backtest_panel

        panel = create_backtest_panel()
        rendered = str(panel)
        for component_id in (
            "backtest-symbol-input",
            "backtest-run-btn",
            "backtest-equity-graph",
            "backtest-windows-table",
            "backtest-slippage-model",
            "backtest-teach-btn",
            "backtest-teach-status",
        ):
            self.assertIn(component_id, rendered)

    def test_callbacks_register_on_fresh_app(self):
        import dash

        from webui.callbacks.backtest_callbacks import register_backtest_callbacks

        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        register_backtest_callbacks(app)
        # Callback map keys are derived from outputs.
        self.assertTrue(
            any("backtest-status" in key for key in app.callback_map),
            f"backtest callback missing from callback map: {list(app.callback_map)}",
        )
        self.assertTrue(
            any("backtest-teach-status" in key for key in app.callback_map),
            f"teach callback missing from callback map: {list(app.callback_map)}",
        )

    def test_metric_formatting_helpers(self):
        from webui.callbacks.backtest_callbacks import _fmt_pct, _fmt_ratio

        self.assertEqual(_fmt_pct(0.25), "+25.00%")
        self.assertEqual(_fmt_pct(-0.031), "-3.10%")
        self.assertEqual(_fmt_pct(None), "—")
        self.assertEqual(_fmt_ratio(1.234), "1.23")
        self.assertEqual(_fmt_ratio(None), "—")

    def test_equity_figure_and_windows_table_render(self):
        from webui.callbacks.backtest_callbacks import (
            _build_equity_figure,
            _build_windows_table,
        )

        curve = pd.Series(
            [100.0, 101.0, 102.0],
            index=pd.date_range("2026-01-05", periods=3),
        )
        figure = _build_equity_figure(curve, "AAPL")
        self.assertEqual(len(figure.data), 1)

        window = {
            "start_date": "2026-01-05",
            "end_date": "2026-02-05",
            "bars": 21,
            "metrics": {
                "cumulative_return": 0.05,
                "sharpe_ratio": 1.5,
                "max_drawdown": 0.02,
                "win_rate": 0.6,
            },
        }
        # A single window adds nothing beyond the full-period metrics.
        self.assertIsNone(_build_windows_table([window]))
        self.assertIsNotNone(_build_windows_table([window, window]))


if __name__ == "__main__":
    unittest.main()


class BacktestRunCallbackTests(unittest.TestCase):
    """The backtest panel replays recorded decisions; the parameters the
    operator picks have to reach the engine, and every refusal has to say
    which one was the problem."""

    def setUp(self):
        import dash

        from webui.callbacks.backtest_callbacks import register_backtest_callbacks

        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        register_backtest_callbacks(app)
        self.app = app

    @staticmethod
    def _result(**overrides):
        from types import SimpleNamespace

        full = SimpleNamespace(
            start_date="2026-01-01",
            end_date="2026-06-30",
            signals_used=42,
            slippage={"model": "fixed", "bps": 5},
            rejected_orders=[],
            metrics={
                "cumulative_return": 0.12,
                "annualized_return": 0.25,
                "sharpe_ratio": 1.4,
                "max_drawdown": -0.08,
                "hit_rate": 0.55,
            },
            equity_curve=pd.Series(
                [100_000.0, 112_000.0],
                index=pd.to_datetime(["2026-01-01", "2026-06-30"]),
            ),
        )
        for key, value in overrides.items():
            setattr(full, key, value)
        return SimpleNamespace(full_period=full, windows=[])

    def _run(self, result=None, error=None, **kwargs):
        from unittest import mock

        from conftest import dash_callback

        captured = {}

        def run_walk_forward(symbol, **options):
            captured["symbol"] = symbol
            captured.update(options)
            if error:
                raise error
            return result if result is not None else self._result()

        with mock.patch(
            "tradingagents.backtest.run_recorded_walk_forward", run_walk_forward
        ):
            answer = dash_callback(self.app, "backtest-status.children")(
                1,
                kwargs.get("symbol", "nvda"),
                kwargs.get("start_date", "2026-01-01"),
                kwargs.get("end_date", "2026-06-30"),
                kwargs.get("window_bars", 63),
                kwargs.get("allow_shorts", False),
                kwargs.get("slippage_model", "fixed"),
            )
        return answer, captured

    def test_a_completed_run_reports_its_window_and_signal_count(self):
        (status, metrics, figure, style, _windows), _captured = self._run()

        rendered = str(status)
        self.assertIn("2026-01-01 → 2026-06-30", rendered)
        self.assertIn("42 recorded signal(s)", rendered)
        self.assertIn("next-bar open", rendered)
        self.assertEqual(style, {"display": "block"})
        self.assertIsNotNone(metrics)
        self.assertIsNotNone(figure)

    def test_the_symbol_is_normalized_before_the_run(self):
        _answer, captured = self._run(symbol="  nvda ")

        self.assertEqual(captured["symbol"], "NVDA")

    def test_the_chosen_parameters_reach_the_engine(self):
        _answer, captured = self._run(
            window_bars=21, allow_shorts=True, slippage_model="volatility"
        )

        self.assertEqual(captured["window_bars"], 21)
        self.assertIs(captured["allow_shorts"], True)
        self.assertEqual(captured["slippage_model"], "volatility")

    def test_unset_parameters_fall_back_to_defaults(self):
        _answer, captured = self._run(
            start_date="", end_date="", window_bars=None, slippage_model=""
        )

        self.assertIsNone(captured["start_date"])
        self.assertIsNone(captured["end_date"])
        self.assertEqual(captured["window_bars"], 63)
        self.assertEqual(captured["slippage_model"], "fixed")

    def test_each_slippage_model_is_described_in_the_status_line(self):
        for model, expected in (
            ({"model": "fixed", "bps": 5}, "5 bps"),
            ({"model": "volatility"}, "volatility-scaled"),
            ({"model": "none"}, "slippage: none"),
            ({"model": "unknown"}, "slippage: n/a"),
        ):
            (status, *_rest), _captured = self._run(
                result=self._result(slippage=model)
            )

            self.assertIn(expected, str(status), model)

    def test_rejected_orders_are_surfaced(self):
        """A gap-through order that never filled would otherwise be invisible."""
        (status, *_rest), _captured = self._run(
            result=self._result(rejected_orders=[{"symbol": "NVDA"}])
        )

        self.assertIn("1 order(s) rejected on gaps", str(status))

    def test_a_blank_symbol_is_refused_without_running(self):
        from unittest import mock

        from conftest import dash_callback

        with mock.patch(
            "tradingagents.backtest.run_recorded_walk_forward"
        ) as run_walk_forward:
            status, metrics, _figure, style, windows = dash_callback(
                self.app, "backtest-status.children"
            )(1, "  ", None, None, 63, False, "fixed")

        run_walk_forward.assert_not_called()
        self.assertIn("Enter a symbol", str(status))
        self.assertIsNone(metrics)
        self.assertIsNone(windows)
        self.assertEqual(style, {"display": "none"})

    def test_a_rejected_configuration_is_reported_as_a_warning(self):
        (status, _metrics, _figure, style, _windows), _captured = self._run(
            error=ValueError("no recorded decisions for NVDA")
        )

        self.assertIn("no recorded decisions", str(status))
        self.assertIn("warning", str(status))
        self.assertEqual(style, {"display": "none"})

    def test_an_unexpected_failure_is_reported_as_an_error(self):
        (status, *_rest), _captured = self._run(error=RuntimeError("price gap"))

        self.assertIn("Backtest failed", str(status))
        self.assertIn("danger", str(status))


class BacktestTeachCallbackTests(unittest.TestCase):
    """Teaching writes lessons into the agent memories, so the summary has to
    distinguish "nothing new" from "nothing teachable"."""

    def setUp(self):
        import dash

        from webui.callbacks.backtest_callbacks import register_backtest_callbacks

        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        register_backtest_callbacks(app)
        self.app = app

    @staticmethod
    def _summary(**overrides):
        summary = {
            "decisions_taught": 4,
            "lessons_written": 9,
            "decisions_skipped_duplicate": 1,
            "decisions_skipped_no_state": 2,
            "outcomes_computed": 7,
        }
        summary.update(overrides)
        return summary

    def _teach(self, summary=None, error=None, symbol="nvda"):
        from unittest import mock

        from conftest import dash_callback

        captured = {}

        def teach(symbol_arg, memories, **options):
            captured["symbol"] = symbol_arg
            captured.update(options)
            if error:
                raise error
            return summary if summary is not None else self._summary()

        with mock.patch("tradingagents.backtest.teach_memories_from_history", teach):
            with mock.patch(
                "tradingagents.backtest.default_agent_memories", lambda: {}
            ):
                answer = dash_callback(self.app, "backtest-teach-status.children")(
                    1, symbol, "2026-01-01", "2026-06-30"
                )
        return answer, captured

    def test_a_successful_teach_reports_what_was_written(self):
        answer, captured = self._teach()

        rendered = str(answer)
        self.assertEqual(captured["symbol"], "NVDA")
        self.assertIn("Taught 4 decision(s)", rendered)
        self.assertIn("9 lesson(s)", rendered)
        self.assertIn("success", rendered)

    def test_the_date_window_reaches_the_teacher(self):
        _answer, captured = self._teach()

        self.assertEqual(captured["start_date"], "2026-01-01")
        self.assertEqual(captured["end_date"], "2026-06-30")

    def test_everything_already_known_is_reported_as_nothing_new(self):
        answer = self._teach(
            self._summary(decisions_taught=0, decisions_skipped_duplicate=3)
        )[0]

        rendered = str(answer)
        self.assertIn("Nothing new to teach", rendered)
        self.assertIn("info", rendered)

    def test_nothing_teachable_names_the_likely_causes(self):
        answer = self._teach(
            self._summary(decisions_taught=0, decisions_skipped_duplicate=0)
        )[0]

        rendered = str(answer)
        self.assertIn("No teachable decisions", rendered)
        self.assertIn("without a recorded final state", rendered)
        self.assertIn("embeddings", rendered)

    def test_a_blank_symbol_is_refused(self):
        from unittest import mock

        from conftest import dash_callback

        with mock.patch(
            "tradingagents.backtest.teach_memories_from_history"
        ) as teach:
            answer = dash_callback(self.app, "backtest-teach-status.children")(
                1, "  ", None, None
            )

        teach.assert_not_called()
        self.assertIn("Enter a symbol", str(answer))

    def test_a_rejected_request_is_reported_as_a_warning(self):
        answer = self._teach(error=ValueError("no recorded outcomes"))[0]

        self.assertIn("no recorded outcomes", str(answer))
        self.assertIn("warning", str(answer))

    def test_an_unexpected_failure_is_reported_as_an_error(self):
        answer = self._teach(error=RuntimeError("vector store down"))[0]

        self.assertIn("Teaching failed", str(answer))
        self.assertIn("danger", str(answer))
