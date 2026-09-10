"""Tests for settings an operator can change without a redeploy.

Precedence is runtime setting > environment > built-in default, which was
a deliberate choice and a consequential one: a redeployed `.env` no longer
restrains what the UI may do, and there is no login in front of the UI.
So most of what is tested here is the accountability that was supposed to
buy — the allow-list, the audit trail, and which transitions count as
arming something.

The store needs only String, Text and DateTime columns, so these run
against in-memory SQLite rather than needing TEST_DATABASE_URL. Settings
that cannot be tested in CI are settings nobody checks.
"""

from __future__ import annotations

import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tradingagents.persistence.postgres.models import (
    RuntimeSettingAuditRow,
    RuntimeSettingRow,
)
from tradingagents.setup.settings import (
    SETTINGS,
    RuntimeSettings,
    apply_overrides,
    setting,
)


def store():
    engine = create_engine("sqlite://")
    RuntimeSettingRow.__table__.create(engine)
    RuntimeSettingAuditRow.__table__.create(engine)
    return RuntimeSettings(sessionmaker(bind=engine))


class AllowListTests(unittest.TestCase):
    """A browser with no authentication in front of it does not get to
    rewrite arbitrary configuration."""

    def test_an_unknown_key_is_refused(self):
        with self.assertRaises(ValueError):
            store().set("database_url", "postgres://evil", actor="someone")

    def test_a_credential_is_not_a_setting(self):
        for key in ("openai_api_key", "alpaca_secret_key", "integration_vault_key"):
            with self.subTest(key=key):
                self.assertIsNone(setting(key))

    def test_a_setting_removed_from_the_list_stops_applying(self):
        """The list is what is permitted now, not what was permitted when
        the row was written."""
        keeper = store()
        keeper.set("autonomous_enabled", True, actor="someone")

        with mock.patch("tradingagents.setup.settings.SETTINGS_BY_KEY", {}):
            config = apply_overrides({"autonomous_enabled": False}, store=keeper)

        self.assertFalse(config["autonomous_enabled"])


class PrecedenceTests(unittest.TestCase):
    def test_a_stored_value_beats_the_configuration(self):
        keeper = store()
        keeper.set("llm_provider", "anthropic", actor="someone")

        config = apply_overrides({"llm_provider": "openai"}, store=keeper)

        self.assertEqual(config["llm_provider"], "anthropic")

    def test_nothing_stored_changes_nothing(self):
        config = apply_overrides({"llm_provider": "openai"}, store=store())

        self.assertEqual(config["llm_provider"], "openai")

    def test_no_store_at_all_changes_nothing(self):
        """No database is a normal state, not an error."""
        config = apply_overrides({"llm_provider": "openai"}, store=None)

        self.assertEqual(config["llm_provider"], "openai")

    def test_an_unreadable_store_changes_nothing(self):
        broken = mock.Mock()
        broken.all.side_effect = RuntimeError("connection refused")

        config = apply_overrides({"llm_provider": "openai"}, store=broken)

        self.assertEqual(config["llm_provider"], "openai")

    def test_booleans_come_back_as_booleans(self):
        keeper = store()
        keeper.set("autonomous_enabled", True, actor="someone")

        config = apply_overrides({}, store=keeper)

        self.assertIs(config["autonomous_enabled"], True)

    def test_clearing_hands_the_answer_back(self):
        keeper = store()
        keeper.set("llm_provider", "anthropic", actor="someone")
        keeper.clear("llm_provider", actor="someone")

        config = apply_overrides({"llm_provider": "openai"}, store=keeper)

        self.assertEqual(config["llm_provider"], "openai")


class DangerTests(unittest.TestCase):
    """Which changes arm something, and which only calm it down."""

    def test_arming_is_dangerous_in_one_direction_only(self):
        cases = (
            ("autonomous_enabled", True, True),
            ("autonomous_enabled", False, False),
            ("execution_gateway", "broker", True),
            ("execution_gateway", "alpaca", True),
            ("execution_gateway", "dry-run", False),
            ("alpaca_use_paper", False, True),
            ("alpaca_use_paper", True, False),
        )
        for key, value, expected in cases:
            with self.subTest(key=key, value=value):
                self.assertEqual(setting(key).is_dangerous_change(value), expected)

    def test_the_legacy_gateway_spelling_is_still_offered(self):
        """`alpaca` is the built-in default and a legal value; dropping it
        from the list would silently rewrite an existing deployment the
        first time anyone saved."""
        values = [value for value, _label in setting("execution_gateway").choices]

        self.assertIn("alpaca", values)

    def test_choosing_a_model_provider_is_not_dangerous(self):
        self.assertFalse(setting("llm_provider").is_dangerous_change("anthropic"))

    def test_every_switch_that_can_reach_a_broker_is_marked(self):
        for key in ("autonomous_enabled", "execution_gateway", "alpaca_use_paper"):
            with self.subTest(key=key):
                self.assertTrue(setting(key).dangerous)
                self.assertIsNotNone(setting(key).safe_value)


class AuditTests(unittest.TestCase):
    """What the environment gave up in authority, this has to give back."""

    def test_a_change_records_who_and_what_it_was_before(self):
        keeper = store()
        keeper.set("execution_gateway", "dry-run", actor="alice")
        keeper.set("execution_gateway", "broker", actor="bob", reason="going live")

        history = keeper.history()

        self.assertEqual(history[0]["actor"], "bob")
        self.assertEqual(history[0]["previous"], "dry-run")
        self.assertEqual(history[0]["new"], "broker")
        self.assertEqual(history[0]["reason"], "going live")

    def test_the_first_change_records_that_there_was_nothing_before(self):
        keeper = store()
        keeper.set("autonomous_enabled", True, actor="alice")

        self.assertIsNone(keeper.history()[0]["previous"])

    def test_clearing_is_recorded_too(self):
        keeper = store()
        keeper.set("llm_provider", "anthropic", actor="alice")
        keeper.clear("llm_provider", actor="bob")

        latest = keeper.history()[0]

        self.assertEqual(latest["previous"], "anthropic")
        self.assertIsNone(latest["new"])

    def test_clearing_something_never_set_records_nothing(self):
        keeper = store()
        keeper.clear("llm_provider", actor="alice")

        self.assertEqual(keeper.history(), [])

    def test_history_is_newest_first(self):
        keeper = store()
        for provider in ("anthropic", "google", "deepseek"):
            keeper.set("llm_provider", provider, actor="alice")

        self.assertEqual(
            [row["new"] for row in keeper.history()],
            ["deepseek", "google", "anthropic"],
        )


class CacheTests(unittest.TestCase):
    def test_a_write_is_visible_immediately(self):
        """The cache must not outlive the change that invalidates it."""
        keeper = store()
        keeper.all()  # warm it
        keeper.set("llm_provider", "anthropic", actor="alice")

        self.assertEqual(keeper.all()["llm_provider"], "anthropic")

    def test_the_ttl_is_short_enough_for_a_worker_cycle(self):
        from tradingagents.setup.settings import CACHE_TTL_SECONDS

        self.assertLessEqual(CACHE_TTL_SECONDS, 30)


class CatalogueTests(unittest.TestCase):
    def test_every_setting_says_what_it_does(self):
        for item in SETTINGS:
            with self.subTest(key=item.key):
                self.assertTrue(item.label)
                self.assertTrue(item.blurb.strip())

    def test_a_choice_offers_choices(self):
        for item in SETTINGS:
            if item.type == "choice" and item.key not in {
                # Filled from the provider registry rather than hard-coded,
                # so that adding a provider does not mean editing this too.
                "llm_provider", "execution_broker",
                "research_market_data_provider",
            }:
                with self.subTest(key=item.key):
                    self.assertTrue(item.choices)

    def test_the_provider_settings_match_the_registry(self):
        from tradingagents.setup.providers import ROLES

        by_setting = {item.setting: item for item in ROLES if item.setting}

        for key in ("llm_provider", "execution_broker", "research_market_data_provider"):
            with self.subTest(key=key):
                self.assertIn(key, by_setting)
                self.assertEqual(setting(key).default, by_setting[key].default)


if __name__ == "__main__":
    unittest.main()
