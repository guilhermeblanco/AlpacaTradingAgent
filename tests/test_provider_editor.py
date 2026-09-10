"""Tests for configuring any one provider from the Set up list.

The wizard skips optional providers deliberately — walking somebody
through Alpha Vantage is the laundry list with a progress bar on it — but
skipping them there and offering no other route left them reachable only
through a collapsed section of the integrations modal. That was a design
error, not a decision, and these tests are mostly about the route that
fixes it: every row in the Set up list opens an editor, whatever its
level, and an editor can put a credential back as well as in.
"""

from __future__ import annotations

import unittest
from unittest import mock

from tradingagents.setup import evaluate_readiness
from tradingagents.setup.providers import role as get_role
from webui.components.provider_editor import provider_form
from webui.components.setup_panel import readiness_summary, requirement_row

BASE = {
    "llm_provider": "openai",
    "execution_broker": "alpaca",
    "persistence_backend": "postgres",
    "database_url": "postgresql+psycopg://x/y",
    "autonomous_analysts": "market,news",
    "autonomous_asset_filter": "stock",
}


def readiness(overrides=None, configured=()):
    have = set(configured)
    return evaluate_readiness(
        {**BASE, **(overrides or {})},
        lookup=lambda key, _env: "vault" if key in have else "",
    )


def text(component) -> str:
    if component is None:
        return ""
    if isinstance(component, str):
        return component
    if isinstance(component, (list, tuple)):
        return " ".join(text(item) for item in component)
    return text(getattr(component, "children", None))


def ids(component, found=None):
    found = [] if found is None else found
    identifier = getattr(component, "id", None)
    if identifier is not None:
        found.append(identifier)
    children = getattr(component, "children", None)
    if isinstance(children, (list, tuple)):
        for child in children:
            ids(child, found)
    elif children is not None:
        ids(children, found)
    return found


def dict_ids(component, id_type):
    return [
        item["key"] if "key" in item else item.get("role")
        for item in ids(component)
        if isinstance(item, dict) and item.get("type") == id_type
    ]


class ReachabilityTests(unittest.TestCase):
    """Every provider has a way in, not just the blocking ones."""

    def test_every_requirement_row_offers_to_configure_it(self):
        view = readiness()

        for requirement in view.requirements:
            with self.subTest(requirement=requirement.id):
                buttons = [
                    item
                    for item in ids(requirement_row(requirement))
                    if isinstance(item, dict)
                    and item.get("type") == "configure-role"
                ]
                self.assertEqual(buttons, [{"type": "configure-role",
                                            "role": requirement.id}])

    def test_the_optional_fallback_is_reachable(self):
        """The specific thing that had no route: Alpha Vantage."""
        view = readiness()
        fallback = view.get("fallback_market_data")

        self.assertEqual(fallback.level, "optional")
        self.assertIn("configure-role", str(ids(requirement_row(fallback))))

    def test_an_already_configured_provider_says_change_rather_than_configure(self):
        view = readiness(configured=["alpha_vantage_api_key"])

        rendered = text(requirement_row(view.get("fallback_market_data")))

        self.assertIn("Change", rendered)
        self.assertNotIn("Configure", rendered)

    def test_the_summary_lists_what_is_configured_and_from_where(self):
        view = readiness(configured=["openai_api_key"])

        rendered = text(readiness_summary(view))

        self.assertIn("encrypted vault", rendered)


class EditorFormTests(unittest.TestCase):
    def _form(self, role_id, chosen, config=None):
        view = readiness(config)
        return provider_form(
            view.get(role_id),
            role=get_role(role_id),
            chosen=chosen,
            id_type="editor-credential",
            picker_type="editor-provider",
            heading=False,
        )

    def test_it_asks_for_the_optional_providers_own_field(self):
        rendered = self._form("fallback_market_data", "alpha_vantage")

        self.assertIn("alpha_vantage_api_key",
                      dict_ids(rendered, "editor-credential"))

    def test_it_uses_its_own_id_namespace(self):
        """The wizard and this editor are both mounted at once, because a
        Bootstrap tab pane stays in the DOM. Sharing field ids would mean
        two components answering to the same id."""
        rendered = self._form("broker", "alpaca")

        self.assertTrue(dict_ids(rendered, "editor-credential"))
        self.assertEqual(dict_ids(rendered, "wizard-credential"), [])

    def test_the_two_namespaces_never_collide(self):
        from webui.callbacks.setup_callbacks import EDITOR_FIELD, EDITOR_PICKER

        self.assertNotEqual(EDITOR_FIELD, "wizard-credential")
        self.assertNotEqual(EDITOR_PICKER, "wizard-provider")

    def test_the_heading_is_left_to_the_modal_title(self):
        rendered = self._form("broker", "alpaca")

        self.assertNotIn("H5", str(type(rendered)))
        self.assertNotIn("Broker", text(rendered).split("Positions")[0])


class EditorCallbackTests(unittest.TestCase):
    def _register(self):
        from webui.callbacks import setup_callbacks

        captured = {}

        class App:
            def callback(self, *_args, **_kwargs):
                def decorate(function):
                    captured[function.__name__] = function
                    return function

                return decorate

        setup_callbacks.register_role_editor_callbacks(App())
        return captured

    def _patch_readiness(self):
        return mock.patch(
            "webui.callbacks.setup_callbacks.current_readiness",
            lambda: readiness(),
        )

    def test_clicking_a_row_opens_the_editor_for_that_role(self):
        captured = self._register()

        with self._patch_readiness(), mock.patch(
            "webui.callbacks.setup_callbacks.current_config", lambda: BASE
        ), mock.patch(
            "webui.callbacks.setup_callbacks.ctx",
            mock.Mock(triggered_id={"type": "configure-role",
                                     "role": "fallback_market_data"}),
        ):
            is_open, title, body, target, _status = captured["open_editor"]([1], None)

        self.assertTrue(is_open)
        self.assertEqual(target, "fallback_market_data")
        self.assertIn("Alpha Vantage", title)
        self.assertIn("alpha_vantage_api_key", dict_ids(body, "editor-credential"))

    def test_a_re_render_does_not_open_it(self):
        """The list refreshes on an interval, which recreates every button
        with n_clicks back at zero."""
        from dash import no_update

        captured = self._register()

        with self._patch_readiness(), mock.patch(
            "webui.callbacks.setup_callbacks.ctx",
            mock.Mock(triggered_id={"type": "configure-role", "role": "broker"}),
        ):
            result = captured["open_editor"]([0, 0, 0], None)

        self.assertEqual(result, (no_update,) * 5)

    def test_cancel_closes_it(self):
        captured = self._register()

        with mock.patch(
            "webui.callbacks.setup_callbacks.ctx",
            mock.Mock(triggered_id="role-editor-cancel"),
        ):
            is_open = captured["open_editor"]([1], 1)[0]

        self.assertFalse(is_open)

    def test_saving_stores_what_was_typed(self):
        captured = self._register()
        vault = mock.Mock()
        vault.set_many.return_value = 1

        with mock.patch(
            "tradingagents.integrations.get_integration_vault", lambda: vault
        ), mock.patch(
            "tradingagents.setup.settings.get_runtime_settings", lambda: None
        ), mock.patch(
            "webui.callbacks.setup_callbacks.current_config", lambda: BASE
        ):
            captured["save_editor"](
                1, "fallback_market_data", [], [],
                [" av-key "],
                [{"type": "editor-credential", "key": "alpha_vantage_api_key"}],
            )

        vault.set_many.assert_called_once()
        self.assertEqual(
            vault.set_many.call_args.args[0], {"alpha_vantage_api_key": "av-key"}
        )

    def test_saving_records_a_provider_change_before_its_fields(self):
        """Otherwise a Tradier token gets filed under an Alpaca
        deployment."""
        captured = self._register()
        order = []
        settings_store = mock.Mock()
        settings_store.set.side_effect = lambda *a, **k: order.append("choice")
        vault = mock.Mock()
        vault.set_many.side_effect = lambda *a, **k: order.append("fields") or 1

        with mock.patch(
            "tradingagents.integrations.get_integration_vault", lambda: vault
        ), mock.patch(
            "tradingagents.setup.settings.get_runtime_settings",
            lambda: settings_store,
        ), mock.patch(
            "webui.callbacks.setup_callbacks.current_config", lambda: BASE
        ):
            captured["save_editor"](
                1, "broker",
                ["tradier"], [{"type": "editor-provider", "role": "broker"}],
                ["token"],
                [{"type": "editor-credential", "key": "tradier_access_token"}],
            )

        self.assertEqual(order, ["choice", "fields"])

    def test_blank_fields_store_nothing(self):
        captured = self._register()
        vault = mock.Mock()

        with mock.patch(
            "tradingagents.integrations.get_integration_vault", lambda: vault
        ), mock.patch(
            "tradingagents.setup.settings.get_runtime_settings", lambda: None
        ), mock.patch(
            "webui.callbacks.setup_callbacks.current_config", lambda: BASE
        ):
            status, _ = captured["save_editor"](
                1, "broker", [], [], ["", "  "],
                [
                    {"type": "editor-credential", "key": "alpaca_api_key"},
                    {"type": "editor-credential", "key": "alpaca_secret_key"},
                ],
            )

        vault.set_many.assert_not_called()
        self.assertIn("Nothing changed", text(status))

    def test_removing_takes_the_keys_back_out(self):
        """Blank means keep, everywhere. Without this there is no way to
        rotate a key out short of psql."""
        captured = self._register()
        vault = mock.Mock()
        vault.delete_many.return_value = 2

        with self._patch_readiness(), mock.patch(
            "tradingagents.integrations.get_integration_vault", lambda: vault
        ), mock.patch(
            "webui.callbacks.setup_callbacks.current_config", lambda: BASE
        ):
            status, _ = captured["clear_editor"](1, "broker")

        vault.delete_many.assert_called_once()
        self.assertEqual(
            sorted(vault.delete_many.call_args.args[0]),
            ["alpaca_api_key", "alpaca_secret_key"],
        )
        self.assertIn("environment value still applies", text(status))

    def test_removing_never_touches_a_non_secret(self):
        captured = self._register()
        vault = mock.Mock()
        vault.delete_many.return_value = 1

        with self._patch_readiness(), mock.patch(
            "tradingagents.integrations.get_integration_vault", lambda: vault
        ), mock.patch(
            "webui.callbacks.setup_callbacks.current_config", lambda: BASE
        ):
            captured["clear_editor"](1, "broker")

        self.assertNotIn("alpaca_use_paper", vault.delete_many.call_args.args[0])

    def test_no_vault_is_reported_rather_than_swallowed(self):
        captured = self._register()

        with mock.patch(
            "tradingagents.integrations.get_integration_vault", lambda: None
        ), mock.patch(
            "tradingagents.setup.settings.get_runtime_settings", lambda: None
        ), mock.patch(
            "webui.callbacks.setup_callbacks.current_config", lambda: BASE
        ):
            status, _ = captured["save_editor"](
                1, "fallback_market_data", [], [], ["k"],
                [{"type": "editor-credential", "key": "alpha_vantage_api_key"}],
            )

        self.assertIn("not configured", text(status))


if __name__ == "__main__":
    unittest.main()
