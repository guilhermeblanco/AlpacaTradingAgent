"""Tests for configured integrations as instances.

The old model had one slot per provider: an "Alpaca broker" you either
filled in or did not. That cannot hold a paper account and a live one at
once, and gives you nowhere to stand while moving between them.

Most of what matters here is the seams. Exactly one instance answers for
a kind; two instances of one provider must not share credentials;
deleting one must not leave a secret nothing points at; and every
existing caller has to keep asking for `alpaca_api_key` and get the
right one without knowing any of this happened.
"""

from __future__ import annotations

import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tradingagents.integrations.instances import (
    IntegrationStore,
    credential_key,
)
from tradingagents.persistence.postgres.models import IntegrationRow


class FakeVault:
    """The vault's surface, without the encryption or the database."""

    def __init__(self):
        self.values: dict[str, str] = {}
        self.deleted: list[str] = []

    def set_many(self, values, *, actor="system"):
        self.values.update(values)
        return len(values)

    def delete_many(self, names, *, actor="system"):
        removed = 0
        for name in names:
            if self.values.pop(name, None) is not None:
                removed += 1
            self.deleted.append(name)
        return removed

    def get(self, name):
        return self.values.get(name)

    def statuses(self, names):
        from tradingagents.integrations.vault import CredentialStatus

        return {
            name: CredentialStatus(
                name=name, configured=name in self.values, source="vault"
            )
            for name in names
        }


def store():
    engine = create_engine("sqlite://")
    IntegrationRow.__table__.create(engine)
    return IntegrationStore(sessionmaker(bind=engine), vault=FakeVault())


class AddingTests(unittest.TestCase):
    def test_an_instance_carries_a_name_you_chose(self):
        keeper = store()
        instance = keeper.add("broker", "alpaca", "paper")

        self.assertEqual(instance.provider, "alpaca")
        self.assertEqual(instance.name, "paper")
        self.assertIn("Alpaca", instance.label)
        self.assertIn("paper", instance.label)

    def test_two_of_one_provider_can_coexist(self):
        """The whole point: a paper account and a live one."""
        keeper = store()
        keeper.add("broker", "alpaca", "paper")
        keeper.add("broker", "alpaca", "live")

        self.assertEqual(len(keeper.list("broker")), 2)

    def test_the_first_of_its_kind_activates_itself(self):
        """Adding your only broker and then having to notice it is
        inactive is a step with no decision in it."""
        keeper = store()
        instance = keeper.add("broker", "alpaca", "paper")

        self.assertTrue(keeper.get(instance.id).active)

    def test_a_second_one_does_not_steal_the_active_slot(self):
        keeper = store()
        first = keeper.add("broker", "alpaca", "paper")
        second = keeper.add("broker", "alpaca", "live")

        self.assertTrue(keeper.get(first.id).active)
        self.assertFalse(keeper.get(second.id).active)

    def test_asking_for_it_to_be_active_makes_it_so(self):
        keeper = store()
        keeper.add("broker", "alpaca", "paper")
        second = keeper.add("broker", "alpaca", "live", activate=True)

        self.assertTrue(keeper.get(second.id).active)


class ActivationTests(unittest.TestCase):
    def test_only_one_answers_for_a_kind(self):
        """Two active brokers is not a state anything downstream knows
        how to read."""
        keeper = store()
        first = keeper.add("broker", "alpaca", "paper")
        second = keeper.add("broker", "tradier", "main")

        keeper.activate(second.id)

        self.assertFalse(keeper.get(first.id).active)
        self.assertTrue(keeper.get(second.id).active)
        self.assertEqual(keeper.active("broker").id, second.id)

    def test_a_kind_whose_sources_contribute_together_may_have_several(self):
        """News is Finnhub and Google News and the hosted search, not a
        choice between them."""
        keeper = store()
        first = keeper.add("news", "finnhub", "")
        second = keeper.add("news", "google_news", "")

        keeper.activate(second.id)

        self.assertTrue(keeper.get(first.id).active)
        self.assertTrue(keeper.get(second.id).active)

    def test_activating_one_kind_leaves_another_alone(self):
        keeper = store()
        model = keeper.add("model", "openai", "production")
        keeper.add("broker", "alpaca", "paper")
        other = keeper.add("broker", "tradier", "main")

        keeper.activate(other.id)

        self.assertTrue(keeper.get(model.id).active)

    def test_activating_something_that_does_not_exist_says_so(self):
        with self.assertRaises(ValueError):
            store().activate("nope")

    def test_deactivating_leaves_the_kind_unanswered(self):
        keeper = store()
        instance = keeper.add("broker", "alpaca", "paper")

        keeper.deactivate(instance.id)

        self.assertIsNone(keeper.active("broker"))


class CredentialTests(unittest.TestCase):
    def test_each_instance_owns_its_own_credentials(self):
        """Two Alpaca instances sharing one key would make the second
        one pointless."""
        keeper = store()
        paper = keeper.add(
            "broker", "alpaca", "paper",
            credentials={"alpaca_api_key": "paper-key"},
        )
        live = keeper.add(
            "broker", "alpaca", "live",
            credentials={"alpaca_api_key": "live-key"},
        )

        self.assertEqual(
            keeper._vault.get(credential_key(paper.id, "alpaca_api_key")),
            "paper-key",
        )
        self.assertEqual(
            keeper._vault.get(credential_key(live.id, "alpaca_api_key")),
            "live-key",
        )

    def test_a_blank_value_stores_nothing(self):
        keeper = store()
        instance = keeper.add("broker", "alpaca", "paper")

        stored = keeper.set_credentials(instance.id, {"alpaca_api_key": "   "})

        self.assertEqual(stored, 0)

    def test_it_reports_which_fields_are_set_and_never_their_values(self):
        keeper = store()
        instance = keeper.add(
            "broker", "alpaca", "paper",
            credentials={"alpaca_api_key": "secret"},
        )

        fetched = keeper.get(instance.id)

        self.assertEqual(fetched.configured_fields, ("alpaca_api_key",))
        self.assertNotIn("secret", repr(fetched))

    def test_deleting_an_instance_takes_its_secrets_with_it(self):
        """Otherwise the key is left behind with nothing pointing at it
        and no way to reach it and rotate it."""
        keeper = store()
        instance = keeper.add(
            "broker", "alpaca", "paper",
            credentials={"alpaca_api_key": "secret"},
        )

        keeper.remove(instance.id)

        self.assertIsNone(keeper.get(instance.id))
        self.assertIn(
            credential_key(instance.id, "alpaca_api_key"), keeper._vault.deleted
        )

    def test_removing_something_absent_is_not_an_error(self):
        store().remove("nope")

    def test_no_vault_is_refused_rather_than_silently_dropped(self):
        engine = create_engine("sqlite://")
        IntegrationRow.__table__.create(engine)
        keeper = IntegrationStore(sessionmaker(bind=engine), vault=None)
        instance = keeper.add("broker", "alpaca", "paper")

        with self.assertRaises(ValueError):
            keeper.set_credentials(instance.id, {"alpaca_api_key": "k"})


class ResolutionTests(unittest.TestCase):
    """Existing callers keep asking for `alpaca_api_key`."""

    def _patched(self, keeper):
        return mock.patch(
            "tradingagents.integrations.instances.get_integration_store",
            lambda: keeper,
        )

    def test_the_active_instance_answers(self):
        from tradingagents.integrations.instances import active_credential

        keeper = store()
        keeper.add(
            "broker", "alpaca", "paper",
            credentials={"alpaca_api_key": "paper-key"},
        )

        with self._patched(keeper):
            self.assertEqual(active_credential("alpaca_api_key"), "paper-key")

    def test_switching_which_is_active_switches_the_answer(self):
        from tradingagents.integrations.instances import active_credential

        keeper = store()
        keeper.add(
            "broker", "alpaca", "paper",
            credentials={"alpaca_api_key": "paper-key"},
        )
        live = keeper.add(
            "broker", "alpaca", "live",
            credentials={"alpaca_api_key": "live-key"},
        )

        with self._patched(keeper):
            self.assertEqual(active_credential("alpaca_api_key"), "paper-key")
            keeper.activate(live.id)
            self.assertEqual(active_credential("alpaca_api_key"), "live-key")

    def test_an_inactive_provider_does_not_answer(self):
        """A Tradier instance must not supply a key when Alpaca is the
        active broker, even though nothing else has one."""
        from tradingagents.integrations.instances import active_credential

        keeper = store()
        keeper.add("broker", "alpaca", "paper")
        tradier = keeper.add(
            "broker", "tradier", "main",
            credentials={"tradier_access_token": "t"},
        )
        keeper.deactivate(tradier.id)

        with self._patched(keeper):
            self.assertIsNone(active_credential("tradier_access_token"))

    def test_no_store_at_all_is_a_normal_state(self):
        from tradingagents.integrations.instances import active_credential

        with mock.patch(
            "tradingagents.integrations.instances.get_integration_store",
            lambda: None,
        ):
            self.assertIsNone(active_credential("alpaca_api_key"))

    def test_a_field_no_provider_declares_resolves_to_nothing(self):
        from tradingagents.integrations.instances import active_credential

        keeper = store()
        with self._patched(keeper):
            self.assertIsNone(active_credential("not_a_field"))

    def test_an_instance_outranks_a_plain_vault_entry(self):
        """Plain entries are what a deployment configured before any of
        this has; instances win so a migration can happen one at a
        time."""
        from tradingagents.dataflows import config as config_module

        keeper = store()
        keeper.add(
            "broker", "alpaca", "paper",
            credentials={"alpaca_api_key": "from-instance"},
        )

        with self._patched(keeper), mock.patch(
            "tradingagents.integrations.get_configured_credential",
            lambda name: "from-plain-vault",
        ):
            resolved = config_module.get_api_key("alpaca_api_key", "ALPACA_API_KEY")

        self.assertEqual(resolved, "from-instance")

    def test_a_plain_entry_still_answers_when_no_instance_does(self):
        from tradingagents.dataflows import config as config_module

        keeper = store()

        with self._patched(keeper), mock.patch(
            "tradingagents.integrations.get_configured_credential",
            lambda name: "from-plain-vault",
        ):
            resolved = config_module.get_api_key("alpaca_api_key", "ALPACA_API_KEY")

        self.assertEqual(resolved, "from-plain-vault")

    def test_the_source_report_names_the_instance(self):
        from tradingagents.dataflows import config as config_module

        keeper = store()
        keeper.add(
            "broker", "alpaca", "paper",
            credentials={"alpaca_api_key": "k"},
        )

        with self._patched(keeper):
            source = config_module.get_api_key_source(
                "alpaca_api_key", "ALPACA_API_KEY"
            )

        self.assertEqual(source, config_module.KEY_SOURCE_INTEGRATION)


if __name__ == "__main__":
    unittest.main()
