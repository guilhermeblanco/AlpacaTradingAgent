"""Tests for the provider description layer.

The point of these is not that the catalogue is well-formed. It is that
the catalogue and the factories agree, because they did not:

  * setup offered `dashscope` and `zhipu`; `create_llm_client` dispatches
    on `qwen` and `glm`, so either choice raised `Unsupported LLM
    provider` at the first model call.
  * setup offered `local`, `lmstudio` and `openai_compatible` as key-free
    local providers; the factory knows `ollama` and `local_openai`.

Both were invisible until someone chose one. A description that names a
provider the code cannot construct is worse than no description, so the
tests below walk every id through the thing that would have to build it.
"""

from __future__ import annotations

import unittest

from tradingagents.setup.providers import (
    BROKER_PROVIDERS,
    MARKET_DATA_PROVIDERS,
    MODEL_PROVIDERS,
    ROLES,
    Field,
    all_fields,
    role,
    selected,
    spec,
)


class FactoryAgreementTests(unittest.TestCase):
    """Every id offered is an id something can build."""

    def test_every_model_provider_is_one_the_factory_dispatches_on(self):
        from tradingagents.llm_clients.factory import _OPENAI_COMPATIBLE

        constructible = set(_OPENAI_COMPATIBLE) | {"google", "anthropic", "azure"}

        for provider in MODEL_PROVIDERS:
            with self.subTest(provider=provider.id):
                self.assertIn(
                    provider.id,
                    constructible,
                    f"{provider.id!r} is offered but create_llm_client would "
                    f"raise Unsupported LLM provider",
                )

    def test_the_old_wrong_names_are_gone(self):
        """Named explicitly so a well-meaning revert fails here."""
        offered = {provider.id for provider in MODEL_PROVIDERS}

        for wrong in ("dashscope", "zhipu", "local", "lmstudio", "openai_compatible"):
            with self.subTest(wrong=wrong):
                self.assertNotIn(wrong, offered)

    def test_the_vendors_own_naming_survives_in_the_credential(self):
        """The id is the factory's; the environment variable is the
        vendor's. Conflating them is what caused the drift."""
        qwen = next(item for item in MODEL_PROVIDERS if item.id == "qwen")

        self.assertEqual(qwen.fields[0].key, "dashscope_api_key")
        self.assertEqual(qwen.fields[0].env_var, "DASHSCOPE_API_KEY")

    def test_every_broker_is_registered(self):
        from tradingagents.broker.registry import BROKER_CAPABILITY_MATRIX

        for provider in BROKER_PROVIDERS:
            with self.subTest(provider=provider.id):
                self.assertIn(provider.id, BROKER_CAPABILITY_MATRIX)

    def test_every_market_data_provider_is_one_the_config_allows(self):
        from tradingagents.configuration import TradingAgentsConfig

        allowed = set(
            TradingAgentsConfig.model_fields["research_market_data_provider"]
            .annotation.__args__
        )

        for provider in MARKET_DATA_PROVIDERS:
            with self.subTest(provider=provider.id):
                self.assertIn(provider.id, allowed)


class RoleTests(unittest.TestCase):
    def test_every_role_has_at_least_one_provider(self):
        for item in ROLES:
            with self.subTest(role=item.id):
                self.assertTrue(item.providers)

    def test_a_role_with_one_implementation_says_so(self):
        """A picker with one entry is a seam, not a choice, and pretending
        otherwise wastes the reader's time."""
        for item in ROLES:
            if len(item.providers) == 1 and item.selection == "one":
                with self.subTest(role=item.id):
                    self.assertTrue(
                        item.single_implementation_note,
                        f"{item.id} offers one provider and does not admit it",
                    )

    def test_a_role_with_real_choice_makes_no_such_excuse(self):
        for item in ROLES:
            if len(item.providers) > 1:
                with self.subTest(role=item.id):
                    self.assertFalse(item.single_implementation_note)

    def test_news_is_a_set_rather_than_a_choice(self):
        """Finnhub, Google News and the hosted search contribute together;
        choosing between them is not the operation."""
        self.assertEqual(role("news").selection, "many")
        self.assertEqual(role("news").setting, "")

    def test_every_chooseable_role_names_the_setting_that_holds_the_choice(self):
        for item in ROLES:
            if item.selection == "one":
                with self.subTest(role=item.id):
                    self.assertTrue(item.setting)
                    self.assertTrue(item.default)

    def test_the_default_is_a_provider_that_exists(self):
        for item in ROLES:
            if item.default:
                with self.subTest(role=item.id):
                    self.assertIsNotNone(item.provider(item.default))


class SelectionTests(unittest.TestCase):
    def test_the_configuration_decides(self):
        self.assertEqual(selected("model", {"llm_provider": "anthropic"}), "anthropic")
        self.assertEqual(selected("broker", {"execution_broker": "tradier"}), "tradier")

    def test_an_unknown_choice_falls_back_to_the_default(self):
        """Rather than returning something no factory can build."""
        self.assertEqual(selected("model", {"llm_provider": "sorcery"}), "openai")

    def test_an_absent_choice_uses_the_default(self):
        self.assertEqual(selected("model", {}), "openai")

    def test_case_and_spacing_do_not_matter(self):
        self.assertEqual(selected("model", {"llm_provider": "  Anthropic "}), "anthropic")

    def test_the_spec_follows_the_selection(self):
        chosen = spec("broker", {"execution_broker": "tradier"})

        self.assertEqual(chosen.id, "tradier")
        self.assertIn("tradier_access_token", [item.key for item in chosen.fields])

    def test_a_set_valued_role_has_no_single_selection(self):
        self.assertEqual(selected("news", {}), "")


class FieldTests(unittest.TestCase):
    def test_a_secret_field_knows_it_is_one(self):
        self.assertTrue(Field("k", "K").secret)
        self.assertFalse(Field("k", "K", "text").secret)

    def test_the_environment_variable_is_the_key_uppercased(self):
        self.assertEqual(Field("alpaca_api_key", "K").env_var, "ALPACA_API_KEY")

    def test_no_two_providers_disagree_about_a_field(self):
        """The same key means the same thing everywhere — it addresses one
        vault row and one environment variable."""
        by_key: dict[str, Field] = {}
        for item in ROLES:
            for provider in item.providers:
                for entry in provider.fields:
                    existing = by_key.setdefault(entry.key, entry)
                    with self.subTest(key=entry.key, provider=provider.id):
                        self.assertEqual(existing.type, entry.type)

    def test_every_secret_field_says_where_to_get_one(self):
        for entry in all_fields():
            if entry.type != "secret":
                continue
            with self.subTest(key=entry.key):
                self.assertTrue(
                    entry.obtain_url,
                    f"{entry.key} is a secret with nowhere to get it",
                )

    def test_a_provider_needing_nothing_explains_itself(self):
        """An empty form looks broken; a sentence does not."""
        for item in ROLES:
            for provider in item.providers:
                if provider.fields:
                    continue
                with self.subTest(provider=provider.id):
                    self.assertTrue(provider.note)


if __name__ == "__main__":
    unittest.main()
