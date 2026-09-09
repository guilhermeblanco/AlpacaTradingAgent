"""Tests for the CLI's interactive prompts.

Every prompt here can be cancelled with Ctrl-C, which makes questionary
return None. The contract is that a cancelled prompt exits rather than
letting None flow into a trading run as a ticker, a date, or a model id.
"""

from __future__ import annotations

import unittest
from unittest import mock

from cli import utils
from cli.models import AnalystType


class Answer:
    """Stands in for a questionary prompt object."""

    def __init__(self, value):
        self._value = value

    def ask(self):
        return self._value


def _answer(value):
    return lambda *args, **kwargs: Answer(value)


def _capture(value):
    """Answer a prompt while recording the kwargs it was built with."""
    captured = {}

    def prompt(*args, **kwargs):
        captured.update(kwargs)
        captured["message"] = args[0] if args else kwargs.get("message")
        return Answer(value)

    return prompt, captured


class TickerPromptTests(unittest.TestCase):
    def test_a_ticker_is_upper_cased_and_stripped(self):
        with mock.patch("questionary.text", _answer("  nvda ")):
            self.assertEqual(utils.get_ticker(), "NVDA")

    def test_cancelling_exits(self):
        with mock.patch("questionary.text", _answer(None)):
            with self.assertRaises(SystemExit) as raised:
                utils.get_ticker()

        self.assertEqual(raised.exception.code, 1)

    def test_an_empty_ticker_exits(self):
        with mock.patch("questionary.text", _answer("")):
            with self.assertRaises(SystemExit):
                utils.get_ticker()

    def test_blank_input_is_rejected_by_the_validator(self):
        prompt, captured = _capture("NVDA")
        with mock.patch("questionary.text", prompt):
            utils.get_ticker()
        validate = captured["validate"]

        self.assertIs(validate("NVDA"), True)
        self.assertNotEqual(validate("   "), True)


class DatePromptTests(unittest.TestCase):
    def test_a_valid_date_is_returned_stripped(self):
        with mock.patch("questionary.text", _answer(" 2026-09-09 ")):
            self.assertEqual(utils.get_analysis_date(), "2026-09-09")

    def test_cancelling_exits(self):
        with mock.patch("questionary.text", _answer(None)):
            with self.assertRaises(SystemExit):
                utils.get_analysis_date()

    def test_the_validator_accepts_only_iso_calendar_dates(self):
        prompt, captured = _capture("2026-09-09")
        with mock.patch("questionary.text", prompt):
            utils.get_analysis_date()
        validate = captured["validate"]

        self.assertIs(validate("2026-09-09"), True)
        for invalid in ("2026-9-9", "09-09-2026", "not a date", "2026-13-01", "2026-02-30"):
            self.assertNotEqual(validate(invalid), True, invalid)


class AnalystSelectionTests(unittest.TestCase):
    def test_every_analyst_type_is_offered(self):
        """An analyst missing from the choice list is unreachable."""
        prompt, captured = _capture([AnalystType.MARKET])
        with mock.patch("questionary.checkbox", prompt):
            selected = utils.select_analysts()

        self.assertEqual(
            [choice.value for choice in captured["choices"]],
            [item.value for item in AnalystType],
        )
        self.assertEqual(selected, [AnalystType.MARKET])

    def test_cancelling_exits(self):
        with mock.patch("questionary.checkbox", _answer(None)):
            with self.assertRaises(SystemExit):
                utils.select_analysts()


class ResearchDepthTests(unittest.TestCase):
    def test_the_selected_round_count_is_returned(self):
        with mock.patch("questionary.select", _answer(3)):
            self.assertEqual(utils.select_research_depth(), 3)

    def test_the_offered_depths_are_ordered_shallow_to_deep(self):
        prompt, captured = _capture(1)
        with mock.patch("questionary.select", prompt):
            utils.select_research_depth()

        values = [choice.value for choice in captured["choices"]]
        self.assertEqual(values, sorted(values))
        self.assertTrue(all(value > 0 for value in values))

    def test_cancelling_exits(self):
        with mock.patch("questionary.select", _answer(None)):
            with self.assertRaises(SystemExit):
                utils.select_research_depth()


class ProviderSelectionTests(unittest.TestCase):
    def test_the_chosen_provider_is_returned(self):
        with mock.patch("questionary.select", _answer("anthropic")):
            self.assertEqual(utils.select_llm_provider(), "anthropic")

    def test_the_offered_providers_come_from_the_registry(self):
        from tradingagents.openai_model_registry import get_llm_provider_options

        prompt, captured = _capture("openai")
        with mock.patch("questionary.select", prompt):
            utils.select_llm_provider()

        self.assertEqual(
            [choice.value for choice in captured["choices"]],
            [option["value"] for option in get_llm_provider_options()],
        )

    def test_cancelling_exits(self):
        with mock.patch("questionary.select", _answer(None)):
            with self.assertRaises(SystemExit):
                utils.select_llm_provider()


class ModelSelectionTests(unittest.TestCase):
    def test_a_listed_quick_model_is_returned(self):
        with mock.patch("questionary.select", _answer("gpt-5.4-nano")):
            self.assertEqual(utils.select_shallow_thinking_agent("openai"), "gpt-5.4-nano")

    def test_a_listed_deep_model_is_returned(self):
        with mock.patch("questionary.select", _answer("gpt-5.4-mini")):
            self.assertEqual(utils.select_deep_thinking_agent("openai"), "gpt-5.4-mini")

    def test_choosing_custom_prompts_for_an_id(self):
        with mock.patch("questionary.select", _answer("custom")), mock.patch(
            "questionary.text", _answer(" my-org/my-model ")
        ):
            self.assertEqual(
                utils.select_shallow_thinking_agent("openrouter"), "my-org/my-model"
            )

    def test_choosing_custom_for_the_deep_model_prompts_too(self):
        with mock.patch("questionary.select", _answer("custom")), mock.patch(
            "questionary.text", _answer("my-org/my-model")
        ):
            self.assertEqual(
                utils.select_deep_thinking_agent("openrouter"), "my-org/my-model"
            )

    def test_cancelling_the_model_choice_exits(self):
        for select in (utils.select_shallow_thinking_agent, utils.select_deep_thinking_agent):
            with mock.patch("questionary.select", _answer(None)):
                with self.assertRaises(SystemExit):
                    select("openai")

    def test_cancelling_the_custom_id_exits(self):
        with mock.patch("questionary.select", _answer("custom")), mock.patch(
            "questionary.text", _answer(None)
        ):
            with self.assertRaises(SystemExit):
                utils.select_shallow_thinking_agent("openrouter")


class CustomModelPromptTests(unittest.TestCase):
    def test_azure_asks_for_a_deployment_name(self):
        prompt, captured = _capture("my-deployment")
        with mock.patch("questionary.text", prompt):
            utils._prompt_custom_model_id("azure", "quick")

        self.assertIn("deployment name", captured["message"])

    def test_other_providers_ask_for_a_model_id(self):
        prompt, captured = _capture("my-org/my-model")
        with mock.patch("questionary.text", prompt):
            utils._prompt_custom_model_id("openrouter", "deep")

        self.assertIn("model ID", captured["message"])
        self.assertIn("deep", captured["message"])

    def test_a_blank_id_is_rejected_by_the_validator(self):
        prompt, captured = _capture("my-model")
        with mock.patch("questionary.text", prompt):
            utils._prompt_custom_model_id("ollama", "quick")
        validate = captured["validate"]

        self.assertIs(validate("my-model"), True)
        self.assertNotEqual(validate("  "), True)


class OptionalPromptTests(unittest.TestCase):
    """These are optional settings, so cancelling falls back rather than exits."""

    def test_output_language_defaults_to_english(self):
        for answer in (None, "", "   "):
            with mock.patch("questionary.text", _answer(answer)):
                self.assertEqual(utils.get_output_language(), "English")

    def test_output_language_is_stripped(self):
        with mock.patch("questionary.text", _answer("  Portuguese ")):
            self.assertEqual(utils.get_output_language(), "Portuguese")

    def test_backend_url_defaults_to_empty(self):
        with mock.patch("questionary.text", _answer(None)):
            self.assertEqual(utils.get_backend_url(), "")

    def test_backend_url_is_stripped(self):
        with mock.patch("questionary.text", _answer(" http://localhost:1234/v1 ")):
            self.assertEqual(utils.get_backend_url(), "http://localhost:1234/v1")

    def test_checkpoint_choice_is_coerced_to_a_bool(self):
        with mock.patch("questionary.confirm", _answer(None)):
            self.assertIs(utils.select_checkpoint_enabled(), False)
        with mock.patch("questionary.confirm", _answer(True)):
            self.assertIs(utils.select_checkpoint_enabled(), True)

    def test_gemini_thinking_defaults_to_provider_default(self):
        with mock.patch("questionary.select", _answer(None)):
            self.assertEqual(utils.ask_gemini_thinking_config(), "")

    def test_gemini_thinking_returns_the_chosen_level(self):
        with mock.patch("questionary.select", _answer("high")):
            self.assertEqual(utils.ask_gemini_thinking_config(), "high")

    def test_anthropic_effort_defaults_to_provider_default(self):
        with mock.patch("questionary.select", _answer(None)):
            self.assertEqual(utils.ask_anthropic_effort(), "")

    def test_anthropic_effort_offers_the_documented_levels(self):
        prompt, captured = _capture("high")
        with mock.patch("questionary.select", prompt):
            utils.ask_anthropic_effort()

        self.assertEqual(
            [choice.value for choice in captured["choices"]],
            ["", "high", "medium", "low"],
        )


if __name__ == "__main__":
    unittest.main()
