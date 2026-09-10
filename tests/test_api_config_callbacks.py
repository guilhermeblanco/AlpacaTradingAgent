"""Tests for the integrations modal's callbacks.

This is where API credentials are entered, stored, and cleared. Two things
matter beyond rendering: a save must reach the encrypted vault rather than
the browser, and when the vault is unavailable the operator must be told
that nothing was saved instead of being shown a success message.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import dash
from dash.exceptions import PreventUpdate

from conftest import dash_callback
from webui.callbacks import api_config_callbacks


class Vault:
    def __init__(self, stored=(), deleted=3, changed=5):
        self.stored = set(stored)
        self._deleted = deleted
        self._changed = changed
        self.set_calls = []
        self.delete_calls = []

    def statuses(self, config_keys):
        return {
            key: SimpleNamespace(configured=key in self.stored) for key in config_keys
        }

    def set_many(self, updates, actor=None):
        self.set_calls.append((updates, actor))
        return self._changed

    def delete_many(self, keys, actor=None):
        self.delete_calls.append((list(keys), actor))
        return self._deleted


class ApiConfigFixture(unittest.TestCase):
    def setUp(self):
        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        api_config_callbacks.register_api_config_callbacks(app)
        self.app = app
        self.api_ids = [item["id"] for item in api_config_callbacks.get_api_configs()]

    def _vault(self, vault):
        patcher = mock.patch.object(
            api_config_callbacks, "get_integration_vault", lambda: vault
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return vault

    def _triggered(self, triggered_id):
        return mock.patch.object(
            api_config_callbacks, "ctx", SimpleNamespace(triggered_id=triggered_id)
        )


class ModalToggleTests(ApiConfigFixture):
    """This modal no longer owns the header button.

    The header opens the Integrations screen now, which lists what is
    configured rather than one field per provider this build can talk
    to. This one stays reachable from Set up, because it is how a plain
    vault entry is set — the arrangement a deployment made before
    instances existed, which still resolves underneath them.
    """

    def _toggle(self, triggered_id, is_open=False):
        with self._triggered(triggered_id):
            return dash_callback(self.app, "api-config-modal.is_open")(0, is_open)

    def test_the_close_button_closes_it(self):
        self.assertFalse(self._toggle("close-api-config-btn", is_open=True))

    def test_anything_else_leaves_it_as_it_was(self):
        self.assertTrue(self._toggle("something-else", is_open=True))

    def test_the_header_button_opens_the_integrations_screen_instead(self):
        from webui.callbacks import integrations_callbacks

        captured = {}

        class App:
            def callback(self, *_args, **_kwargs):
                def decorate(function):
                    captured[function.__name__] = function
                    return function

                return decorate

        integrations_callbacks.register_integrations_callbacks(App())

        # That module has its own `ctx` binding; the fixture patches this
        # one's.
        with mock.patch.object(
            integrations_callbacks,
            "ctx",
            SimpleNamespace(triggered_id="open-api-config-btn"),
        ):
            self.assertTrue(captured["toggle"](1, None))


class PasswordVisibilityTests(ApiConfigFixture):
    def _toggle(self, clicks, current_type):
        return dash_callback(self.app, f"api-input-{self.api_ids[0]}.type")(
            clicks, current_type
        )

    def test_revealing_a_key_switches_the_field_and_the_icon(self):
        field_type, icon = self._toggle(1, "password")

        self.assertEqual(field_type, "text")
        self.assertIn("eye-slash", icon)

    def test_hiding_it_again_switches_back(self):
        field_type, icon = self._toggle(1, "text")

        self.assertEqual(field_type, "password")
        self.assertIn("fa-eye", icon)
        self.assertNotIn("slash", icon)

    def test_no_click_changes_nothing(self):
        with self.assertRaises(PreventUpdate):
            self._toggle(None, "password")


class IntegrationStatusTests(ApiConfigFixture):
    def _load(self, vault):
        self._vault(vault)
        return dash_callback(self.app, "env-file-status.children")(True, None)

    def _fields(self, result):
        count = len(self.api_ids)
        return {
            "values": result[:count],
            "colors": result[count + 1 : count * 2 + 1],
            "titles": result[count * 2 + 1 : count * 3 + 1],
            "summary": result[-1],
        }

    def test_no_secret_value_is_ever_sent_to_the_browser(self):
        """The fields are write-only; the status badge is what is shown."""
        fields = self._fields(self._load(Vault(stored=["openai_api_key"])))

        self.assertEqual(set(fields["values"]), {""})

    def test_a_vaulted_credential_is_reported_as_stored(self):
        fields = self._fields(self._load(Vault(stored=["openai_api_key"])))

        self.assertIn("Encrypted vault", fields["titles"])
        self.assertIn("success", fields["colors"])

    def test_an_environment_credential_is_reported_as_such(self):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-real"}):
            fields = self._fields(self._load(Vault()))

        self.assertIn("Environment", fields["titles"])

    def test_a_placeholder_environment_value_does_not_count(self):
        """`.env.sample` ships `your_...` placeholders."""
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "your_key_here"}):
            fields = self._fields(self._load(Vault()))

        self.assertNotIn("Environment", fields["titles"])

    def test_an_unset_credential_is_reported_as_not_configured(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            fields = self._fields(self._load(Vault()))

        self.assertIn("Not configured", fields["titles"])
        self.assertIn("outline-secondary", fields["colors"])

    def test_the_summary_counts_both_sources(self):
        with mock.patch.dict("os.environ", {"FINNHUB_API_KEY": "real"}):
            fields = self._fields(self._load(Vault(stored=["openai_api_key"])))

        rendered = str(fields["summary"])
        self.assertIn("1 stored", rendered)
        self.assertIn("1 environment", rendered)

    def test_an_unavailable_vault_says_environment_credentials_still_work(self):
        fields = self._fields(self._load(None))

        rendered = str(fields["summary"])
        self.assertIn("Encrypted storage is unavailable", rendered)
        self.assertIn("environment credentials remain usable", rendered)


class SaveTests(ApiConfigFixture):
    def _mutate(self, triggered_id, vault, values=None, paper=True,
                broker="alpaca", provider="alpaca"):
        self._vault(vault)
        secrets = values if values is not None else [""] * len(self.api_ids)
        with self._triggered(triggered_id):
            with mock.patch.object(api_config_callbacks, "set_config") as set_config:
                result = dash_callback(self.app, "api-keys-store.data")(
                    1, 0, *secrets, paper, broker, provider
                )
        return result, set_config

    def test_saving_writes_the_entered_values_to_the_vault(self):
        vault = Vault()
        values = ["sk-new"] + [""] * (len(self.api_ids) - 1)

        (_revision, message), _set_config = self._mutate(
            "save-api-keys-btn", vault, values
        )

        stored, actor = vault.set_calls[0]
        self.assertEqual(stored["openai_api_key"], "sk-new")
        self.assertEqual(actor, "webui")
        self.assertIn("Saved 5", str(message))

    def test_blank_fields_do_not_erase_a_stored_credential(self):
        """An empty box means "leave it alone", not "clear it"."""
        vault = Vault()

        self._mutate("save-api-keys-btn", vault)

        stored, _actor = vault.set_calls[0]
        self.assertNotIn("openai_api_key", stored)

    def test_the_paper_flag_is_stored_as_text(self):
        vault = Vault()

        self._mutate("save-api-keys-btn", vault, paper=False)

        self.assertEqual(vault.set_calls[0][0]["alpaca_use_paper"], "false")

    def test_the_chosen_broker_and_provider_are_stored_and_applied(self):
        vault = Vault()

        _result, set_config = self._mutate(
            "save-api-keys-btn", vault, broker="tradier", provider="tradier"
        )

        stored, _actor = vault.set_calls[0]
        self.assertEqual(stored["execution_broker"], "tradier")
        self.assertEqual(stored["research_market_data_provider"], "tradier")
        set_config.assert_called_once()

    def test_an_unset_broker_falls_back_to_alpaca(self):
        vault = Vault()

        self._mutate("save-api-keys-btn", vault, broker=None, provider=None)

        stored, _actor = vault.set_calls[0]
        self.assertEqual(stored["execution_broker"], "alpaca")

    def test_clearing_removes_the_stored_values_and_says_so(self):
        vault = Vault()

        (_revision, message), _set_config = self._mutate(
            "clear-api-keys-btn", vault
        )

        keys, actor = vault.delete_calls[0]
        self.assertIn("openai_api_key", keys)
        self.assertIn("alpaca_use_paper", keys)
        self.assertEqual(actor, "webui")
        self.assertIn("Disconnected 3", str(message))
        self.assertIn("Environment fallbacks were not changed", str(message))

    def test_without_a_vault_nothing_is_saved_and_the_operator_is_told(self):
        (_revision, message), _set_config = self._mutate("save-api-keys-btn", None)

        rendered = str(message)
        self.assertIn("No credentials were saved", rendered)
        self.assertIn("danger", rendered)

    def test_an_unrecognized_trigger_changes_nothing(self):
        with self.assertRaises(PreventUpdate):
            self._mutate("something-else", Vault())


class BrokerHealthTests(ApiConfigFixture):
    def _check(self, checks=(), ready=True, error=None, clicks=1, broker="alpaca"):
        report = SimpleNamespace(ready=ready, checks=list(checks))

        def certify(runtime, symbol=None, require_paper=None):
            if error:
                raise error
            return report

        with mock.patch(
            "tradingagents.broker.preflight.certify_broker_runtime", certify
        ):
            with mock.patch(
                "tradingagents.broker.registry.get_execution_broker_runtime",
                lambda config: SimpleNamespace(config=config),
            ):
                return dash_callback(self.app, "integration-health-results.children")(
                    clicks, broker
                )

    @staticmethod
    def _check_row(status, message):
        return SimpleNamespace(status=SimpleNamespace(value=status), message=message)

    def test_a_healthy_broker_reports_each_passing_check(self):
        rendered = str(
            self._check([self._check_row("pass", "Credentials accepted")])
        )

        self.assertIn("Credentials accepted", rendered)
        self.assertIn("check-circle", rendered)
        self.assertIn("success", rendered)

    def test_a_warning_check_is_distinguished_from_a_failure(self):
        rendered = str(
            self._check(
                [
                    self._check_row("warn", "Live account"),
                    self._check_row("fail", "Market data unavailable"),
                ],
                ready=False,
            )
        )

        self.assertIn("exclamation-circle", rendered)
        self.assertIn("times-circle", rendered)
        self.assertIn("danger", rendered)

    def test_the_selected_broker_is_the_one_tested(self):
        captured = {}

        with mock.patch(
            "tradingagents.broker.preflight.certify_broker_runtime",
            lambda runtime, symbol=None, require_paper=None: SimpleNamespace(
                ready=True, checks=[]
            ),
        ):
            with mock.patch(
                "tradingagents.broker.registry.get_execution_broker_runtime",
                lambda config: captured.update(config) or SimpleNamespace(),
            ):
                dash_callback(self.app, "integration-health-results.children")(
                    1, "tradier"
                )

        self.assertEqual(captured["execution_broker"], "tradier")

    def test_an_unreachable_broker_reports_the_reason(self):
        rendered = str(self._check(error=RuntimeError("credentials rejected")))

        self.assertIn("Connection test failed", rendered)
        self.assertIn("credentials rejected", rendered)

    def test_no_click_does_nothing(self):
        with self.assertRaises(PreventUpdate):
            self._check(clicks=None)


if __name__ == "__main__":
    unittest.main()
