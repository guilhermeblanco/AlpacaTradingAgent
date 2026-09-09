"""Tests for the configuration panel's logic.

control_callbacks.py is the largest WebUI module and drives what a run is
actually configured with — symbols, models, and which scheduling mode is
armed — but almost none of it was covered.
"""

from __future__ import annotations

import unittest

from webui.callbacks.control_callbacks import (
    _asset_detail,
    _collect_llm_params,
    _custom_model_group_style,
    _custom_model_placeholder,
    _format_symbol_text,
    _llm_group_style,
    _normalize_symbol,
    _parse_symbol_text,
    _resolve_runtime_model,
    _select_options,
    _status_panel,
    _symbol_chip,
    _symbol_suggestion_button,
    format_trading_hours,
    resolve_scheduling_modes,
)


class SymbolParsingTests(unittest.TestCase):
    def test_symbols_are_upper_cased_and_stripped(self):
        self.assertEqual(_normalize_symbol("  nvda "), "NVDA")

    def test_trailing_separators_are_removed(self):
        self.assertEqual(_normalize_symbol("AAPL,"), "AAPL")
        self.assertEqual(_normalize_symbol("AAPL;"), "AAPL")

    def test_inner_whitespace_is_removed(self):
        """Crypto pairs get pasted with spaces around the slash."""
        self.assertEqual(_normalize_symbol("BTC / USD"), "BTC/USD")

    def test_blank_input_normalizes_to_empty(self):
        for value in (None, "", "   ", ","):
            self.assertEqual(_normalize_symbol(value), "")

    def test_comma_and_semicolon_are_both_separators(self):
        self.assertEqual(_parse_symbol_text("nvda, aapl; msft"), ["NVDA", "AAPL", "MSFT"])

    def test_duplicates_are_dropped_keeping_first_order(self):
        self.assertEqual(_parse_symbol_text("AAPL, nvda, aapl"), ["AAPL", "NVDA"])

    def test_a_list_is_accepted_as_well_as_a_string(self):
        self.assertEqual(_parse_symbol_text([" nvda ", "AAPL"]), ["NVDA", "AAPL"])

    def test_empty_entries_are_skipped(self):
        self.assertEqual(_parse_symbol_text("AAPL,,  ,NVDA"), ["AAPL", "NVDA"])

    def test_none_parses_to_no_symbols(self):
        self.assertEqual(_parse_symbol_text(None), [])

    def test_formatting_round_trips_through_parsing(self):
        self.assertEqual(_format_symbol_text("aapl,,nvda; aapl"), "AAPL, NVDA")

    def test_mixed_equity_and_crypto_survive_together(self):
        self.assertEqual(
            _parse_symbol_text("NVDA, eth/usd, BTC / USD"),
            ["NVDA", "ETH/USD", "BTC/USD"],
        )


class SymbolChipTests(unittest.TestCase):
    def test_chip_carries_a_removal_target_for_its_symbol(self):
        rendered = str(_symbol_chip("NVDA"))

        self.assertIn("symbol-chip-remove", rendered)
        self.assertIn("NVDA", rendered)

    def test_suggestion_button_carries_symbol_and_detail(self):
        rendered = str(_symbol_suggestion_button("NVDA", "NVIDIA Corp · stock"))

        self.assertIn("symbol-suggestion-option", rendered)
        self.assertIn("NVIDIA Corp", rendered)

    def test_asset_detail_joins_the_fields_that_are_present(self):
        self.assertEqual(
            _asset_detail(
                {"name": "NVIDIA Corp", "asset_type": "stock", "exchange": "NASDAQ"}
            ),
            "NVIDIA Corp · stock · NASDAQ",
        )

    def test_asset_detail_skips_missing_fields(self):
        self.assertEqual(_asset_detail({"name": "NVIDIA Corp"}), "NVIDIA Corp")
        self.assertEqual(_asset_detail({}), "")


class ModelSelectionTests(unittest.TestCase):
    def test_custom_model_input_is_hidden_unless_custom_is_chosen(self):
        hidden = {"display": "none"}

        self.assertEqual(_custom_model_group_style("openai", "gpt-5.4-mini"), hidden)

    def test_placeholder_is_provider_specific(self):
        self.assertEqual(_custom_model_placeholder("ollama", "quick"), "qwen3:latest")
        self.assertEqual(
            _custom_model_placeholder("openrouter", "deep"), "openai/gpt-5.4-mini"
        )

    def test_azure_placeholder_names_the_deployment_role(self):
        self.assertEqual(
            _custom_model_placeholder("azure", "quick"), "quick-deployment-name"
        )

    def test_unknown_provider_falls_back_to_a_generic_placeholder(self):
        self.assertEqual(
            _custom_model_placeholder("something-new", "quick"), "provider/model-name"
        )

    def test_provider_matching_is_case_insensitive(self):
        self.assertEqual(_custom_model_placeholder("OLLAMA", "quick"), "qwen3:latest")

    def test_a_selected_model_resolves_without_an_error(self):
        resolved, error = _resolve_runtime_model("quick", "gpt-5.4-nano", None)

        self.assertEqual(resolved, "gpt-5.4-nano")
        self.assertIsNone(error)

    def test_custom_without_an_id_reports_which_role_is_missing(self):
        resolved, error = _resolve_runtime_model("deep", "custom", "")

        self.assertIsNone(resolved)
        self.assertIn("deep", error)

    def test_custom_with_an_id_resolves_to_it(self):
        resolved, error = _resolve_runtime_model("deep", "custom", "my-org/my-model")

        self.assertEqual(resolved, "my-org/my-model")
        self.assertIsNone(error)


class LlmParamTests(unittest.TestCase):
    def test_collected_params_are_normalized_for_the_model(self):
        params = _collect_llm_params(
            "gpt-5.4-mini",
            "deep",
            "high",
            "medium",
            "auto",
            None,
            None,
            2048,
            False,
            True,
        )

        self.assertIsInstance(params, dict)
        self.assertEqual(params.get("max_output_tokens"), 2048)


class PanelHelperTests(unittest.TestCase):
    def test_group_style_toggles_visibility(self):
        self.assertEqual(_llm_group_style(True), {})
        self.assertEqual(_llm_group_style(False), {"display": "none"})

    def test_select_options_pair_each_value_with_itself(self):
        self.assertEqual(
            _select_options(["low", "high"]),
            [{"label": "low", "value": "low"}, {"label": "high", "value": "high"}],
        )

    def test_status_panel_renders_title_body_and_pills(self):
        rendered = str(
            _status_panel("Deep research", body="4 rounds", items=["bull", "bear"])
        )

        self.assertIn("Deep research", rendered)
        self.assertIn("4 rounds", rendered)
        self.assertIn("bull", rendered)

    def test_status_panel_tone_reaches_the_class_name(self):
        self.assertIn("warning", str(_status_panel("Careful", tone="warning")))


class TradingHourFormatTests(unittest.TestCase):
    def test_morning_hours_render_as_am(self):
        self.assertEqual(format_trading_hours([9, 10, 11]), "9:00 AM and 10:00 AM and 11:00 AM")

    def test_noon_is_pm_not_zero(self):
        self.assertEqual(format_trading_hours([12]), "12:00 PM")

    def test_afternoon_hours_subtract_twelve(self):
        self.assertEqual(format_trading_hours([13, 16]), "1:00 PM and 4:00 PM")

    def test_no_hours_renders_empty(self):
        self.assertEqual(format_trading_hours([]), "")

    def test_every_valid_market_hour_formats(self):
        """validate_market_hours accepts 9 through 16 inclusive."""
        from webui.utils.market_hours import MARKET_CLOSE_HOUR, MARKET_OPEN_HOUR

        for hour in range(MARKET_OPEN_HOUR, MARKET_CLOSE_HOUR + 1):
            rendered = format_trading_hours([hour])
            self.assertRegex(rendered, r"^(1[0-2]|[1-9]):00 (AM|PM)$", hour)


class SchedulingModeTests(unittest.TestCase):
    """At most one scheduling mode may be armed at a time."""

    def test_enabling_loop_disables_the_other_modes(self):
        loop, market, screener, loop_off, market_off, screener_off = (
            resolve_scheduling_modes("loop-enabled", True, True, True)
        )

        self.assertEqual((loop, market, screener), (True, False, False))
        self.assertFalse(loop_off)
        self.assertTrue(market_off)
        self.assertTrue(screener_off)

    def test_enabling_market_hours_disables_the_other_modes(self):
        self.assertEqual(
            resolve_scheduling_modes("market-hour-enabled", True, True, True),
            (False, True, False, True, False, True),
        )

    def test_enabling_the_screener_disables_the_other_modes(self):
        self.assertEqual(
            resolve_scheduling_modes("screener-enabled", True, True, True),
            (False, False, True, True, True, False),
        )

    def test_only_one_mode_is_ever_left_on(self):
        for trigger in ("loop-enabled", "market-hour-enabled", "screener-enabled"):
            states = resolve_scheduling_modes(trigger, True, True, True)[:3]
            self.assertEqual(sum(bool(state) for state in states), 1, trigger)

    def test_each_armed_mode_keeps_its_own_input_editable(self):
        for trigger, index in (
            ("loop-enabled", 3),
            ("market-hour-enabled", 4),
            ("screener-enabled", 5),
        ):
            result = resolve_scheduling_modes(trigger, True, True, True)
            self.assertFalse(result[index], trigger)

    def test_disabling_a_mode_frees_every_input(self):
        self.assertEqual(
            resolve_scheduling_modes("loop-enabled", False, False, False),
            (False, False, False, True, True, True),
        )

    def test_no_trigger_leaves_the_switches_alone(self):
        self.assertEqual(
            resolve_scheduling_modes(None, True, False, False),
            (True, False, False, False, False, False),
        )


if __name__ == "__main__":
    unittest.main()
