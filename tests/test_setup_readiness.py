"""Tests for what a deployment actually needs configured.

The Integrations screen listed sixteen credentials and called twelve of them
required. The truth is that a default install needs three, and which three
follows from choices made elsewhere — so these tests are mostly about the
*conditions*, not the catalogue. A requirement that is always required
teaches nobody anything; one that appears because you turned the macro
analyst on, and names that as the reason, is the whole point.
"""

from __future__ import annotations

import unittest

from tradingagents.setup.readiness import (
    LOCAL_PROVIDERS,
    MODEL_PROVIDERS,
    evaluate_readiness,
    setup_steps,
)

BASE = {
    "llm_provider": "openai",
    "execution_broker": "alpaca",
    "persistence_backend": "postgres",
    "database_url": "postgresql+psycopg://x/y",
    "autonomous_analysts": "market,social,news,fundamentals,macro",
    "autonomous_asset_filter": "all",
}


def nothing_configured(_key, _env):
    return ""


def everything_configured(_key, _env):
    return "vault"


def configured(*keys):
    """A lookup where exactly these keys resolve."""
    wanted = set(keys)
    return lambda key, _env: "vault" if key in wanted else ""


def readiness(overrides=None, lookup=nothing_configured):
    return evaluate_readiness({**BASE, **(overrides or {})}, lookup=lookup)


class ModelProviderTests(unittest.TestCase):
    """You need one provider key, not ten."""

    def test_only_the_selected_provider_is_asked_for(self):
        view = readiness({"llm_provider": "anthropic"})

        model = view.get("model")
        self.assertEqual(
            [item.key for item in model.credentials], ["anthropic_api_key"]
        )

    def test_no_other_provider_appears_anywhere(self):
        view = readiness({"llm_provider": "anthropic"})

        mentioned = {item.key for item in view.credentials}
        others = {
            credential.key
            for name, credential in MODEL_PROVIDERS.items()
            if name != "anthropic"
        }

        self.assertEqual(mentioned & others, set())

    def test_the_requirement_names_the_setting_that_chose_it(self):
        """So a reader can see it is a consequence, not a fact."""
        view = readiness({"llm_provider": "google"})

        self.assertIn("llm_provider", view.get("model").because)

    def test_it_says_out_loud_that_the_rest_are_alternatives(self):
        view = readiness({"llm_provider": "openai"})

        self.assertTrue(
            any("alternatives, not" in note for note in view.notes), view.notes
        )

    def test_a_local_provider_needs_no_key_at_all(self):
        for provider in sorted(LOCAL_PROVIDERS):
            view = readiness({"llm_provider": provider})

            with self.subTest(provider=provider):
                self.assertEqual(view.get("model").credentials, ())
                self.assertFalse(view.get("model").blocking)

    def test_an_unknown_provider_is_reported_rather_than_ignored(self):
        view = readiness({"llm_provider": "sorcery"})

        self.assertTrue(
            any("not a provider this build knows" in note for note in view.notes)
        )


class BrokerTests(unittest.TestCase):
    def test_the_broker_decides_which_credentials_apply(self):
        alpaca = readiness({"execution_broker": "alpaca"}).get("broker")
        tradier = readiness({"execution_broker": "tradier"}).get("broker")

        self.assertEqual(
            [item.key for item in alpaca.credentials],
            ["alpaca_api_key", "alpaca_secret_key"],
        )
        self.assertEqual(
            [item.key for item in tradier.credentials],
            ["tradier_access_token", "tradier_account_id"],
        )

    def test_a_partly_filled_broker_is_still_unsatisfied(self):
        """Alpaca needs both halves; one of them is not most of the way."""
        view = readiness(lookup=configured("alpaca_api_key"))

        broker = view.get("broker")

        self.assertFalse(broker.satisfied)
        self.assertEqual([item.key for item in broker.missing], ["alpaca_secret_key"])

    def test_market_data_is_only_separate_when_it_is_elsewhere(self):
        same = readiness({"research_market_data_provider": "alpaca"})
        different = readiness({"research_market_data_provider": "tradier"})

        self.assertIsNone(same.get("market_data"))
        self.assertIsNotNone(different.get("market_data"))

    def test_a_separate_data_provider_says_how_to_stop_needing_it(self):
        view = readiness({"research_market_data_provider": "tradier"})

        self.assertIn("goes away", view.get("market_data").because)


class ConditionTests(unittest.TestCase):
    """The requirements that exist only because of a choice."""

    def test_fred_is_required_only_while_the_macro_analyst_runs(self):
        on = readiness({"autonomous_analysts": "market,macro"}).get("macro")
        off = readiness({"autonomous_analysts": "market,news"}).get("macro")

        self.assertEqual(on.level, "conditional")
        self.assertTrue(on.blocking)
        self.assertEqual(off.level, "optional")
        self.assertFalse(off.blocking)

    def test_a_conditional_requirement_names_its_condition(self):
        view = readiness({"autonomous_analysts": "market,macro"})

        self.assertIn("macro analyst", view.get("macro").because)

    def test_crypto_news_follows_the_asset_filter(self):
        for assets, level in (
            ("all", "recommended"),
            ("crypto", "recommended"),
            ("stock", "optional"),
        ):
            view = readiness({"autonomous_asset_filter": assets})

            with self.subTest(assets=assets):
                self.assertEqual(view.get("crypto_news").level, level)

    def test_finnhub_says_what_gets_worse_rather_than_what_breaks(self):
        """It is recommended, not required: the news analyst still runs."""
        view = readiness()

        finnhub = view.get("equity_news")

        self.assertEqual(finnhub.level, "recommended")
        self.assertFalse(finnhub.blocking)
        self.assertIn("Google News", finnhub.why)


class ReadyTests(unittest.TestCase):
    def test_a_bare_deployment_is_not_ready(self):
        self.assertFalse(readiness().ready)

    def test_the_two_required_things_are_enough(self):
        """Three keys, on the default configuration with macro off."""
        view = readiness(
            {"autonomous_analysts": "market,news"},
            lookup=configured(
                "openai_api_key", "alpaca_api_key", "alpaca_secret_key"
            ),
        )

        self.assertTrue(view.ready, [item.title for item in view.blocking])

    def test_a_recommended_gap_does_not_block(self):
        view = readiness(
            {"autonomous_analysts": "market,news"},
            lookup=configured(
                "openai_api_key", "alpaca_api_key", "alpaca_secret_key"
            ),
        )

        self.assertFalse(view.get("equity_news").satisfied)
        self.assertTrue(view.ready)

    def test_a_conditional_gap_does_block(self):
        """Turning the macro analyst on makes FRED as blocking as the model."""
        view = readiness(
            {"autonomous_analysts": "market,macro"},
            lookup=configured(
                "openai_api_key", "alpaca_api_key", "alpaca_secret_key"
            ),
        )

        self.assertFalse(view.ready)
        self.assertEqual([item.id for item in view.blocking], ["macro"])

    def test_everything_configured_is_ready(self):
        self.assertTrue(readiness(lookup=everything_configured).ready)

    def test_the_source_of_each_credential_is_reported(self):
        view = readiness(lookup=lambda key, _env: "environment" if key == "openai_api_key" else "")

        self.assertEqual(view.get("model").sources["openai_api_key"], "environment")


class PersistenceTests(unittest.TestCase):
    def test_a_local_backend_explains_why_the_workbench_is_empty(self):
        view = readiness({"persistence_backend": "local"})

        self.assertTrue(
            any("workbench panels stay empty" in note for note in view.notes),
            view.notes,
        )

    def test_postgres_without_a_url_is_blocking(self):
        view = readiness({"persistence_backend": "postgres", "database_url": ""})

        self.assertTrue(view.get("database").blocking)
        self.assertTrue(any("stay blank" in note for note in view.notes))


class WizardOrderTests(unittest.TestCase):
    def test_the_model_is_asked_for_before_the_broker(self):
        """Equally required; not equally sensible to ask first."""
        steps = [item.id for item in setup_steps(readiness())]

        self.assertLess(steps.index("model"), steps.index("broker"))

    def test_satisfied_requirements_drop_out(self):
        view = readiness(lookup=configured("openai_api_key"))

        self.assertNotIn("model", [item.id for item in setup_steps(view)])

    def test_plainly_optional_things_are_never_asked_for(self):
        """A wizard that walks you through Alpha Vantage is the laundry list
        with a progress bar on it."""
        steps = [item.id for item in setup_steps(readiness())]

        self.assertNotIn("fallback_market_data", steps)

    def test_a_requirement_made_optional_by_a_choice_is_not_asked_for(self):
        steps = [item.id for item in setup_steps(readiness({"autonomous_analysts": "market"}))]

        self.assertNotIn("macro", steps)

    def test_a_fully_configured_deployment_has_no_steps(self):
        self.assertEqual(setup_steps(readiness(lookup=everything_configured)), [])


class DefaultConfigTests(unittest.TestCase):
    def test_it_reads_the_process_configuration_when_given_none(self):
        """Called with no argument anywhere in the app, it must not throw."""
        view = evaluate_readiness(lookup=nothing_configured)

        self.assertTrue(view.requirements)


if __name__ == "__main__":
    unittest.main()


class IntegrationsGroupingTests(unittest.TestCase):
    """The Integrations screen, reshaped by the same model.

    The wizard asks for two or three things; this screen still has to show
    all sixteen, because a credential you cannot find is worse than one you
    have to expand. What changed is that they are no longer sixteen
    equally-weighted rows with twelve of them marked "Required".
    """

    def test_the_config_key_is_derivable_from_the_environment_variable(self):
        """So there is one table, not two that can drift apart."""
        from webui.callbacks.api_config_callbacks import CONFIG_KEY_BY_ID
        from webui.components.api_config_modal import API_CONFIGS, config_key_for

        for entry in API_CONFIGS:
            with self.subTest(api=entry["id"]):
                self.assertEqual(
                    config_key_for(entry), CONFIG_KEY_BY_ID[entry["id"]]
                )

    def test_every_credential_still_appears_somewhere(self):
        """Grouping must not lose one: the save callback gathers all of
        them by id, and a missing input is a silently unsavable key."""
        from webui.components.api_config_modal import API_CONFIGS, grouped_credentials

        def ids(component, found=None):
            found = set() if found is None else found
            identifier = getattr(component, "id", None)
            if isinstance(identifier, str):
                found.add(identifier)
            children = getattr(component, "children", None)
            if isinstance(children, (list, tuple)):
                for child in children:
                    ids(child, found)
            elif children is not None:
                ids(children, found)
            return found

        rendered = ids(grouped_credentials())

        for entry in API_CONFIGS:
            with self.subTest(api=entry["id"]):
                self.assertIn(f"api-input-{entry['id']}", rendered)

    def test_the_unused_model_providers_are_grouped_out_of_the_way(self):
        from webui.components.api_config_modal import grouped_credentials

        titles = [item.title for item in grouped_credentials().children]

        self.assertTrue(
            any("Other model providers" in title for title in titles), titles
        )

    def test_the_group_that_matters_is_the_one_open(self):
        from webui.components.api_config_modal import grouped_credentials

        self.assertEqual(grouped_credentials().active_item, "api-group-in-use")
