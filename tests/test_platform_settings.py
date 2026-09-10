"""Tests for the settings panel that can arm a trading system.

Precedence was set to runtime-wins, which means a browser with no
authentication in front of it can now turn the autonomous worker on and
point it at a live account. That was a deliberate choice; these tests are
about the parts that make it survivable.

The asymmetry is the load-bearing bit. Making the system able to do more
is a confirmation with the consequence spelled out and a reason recorded.
Making it do less is one click. A safety control that is slower to use
than the thing it protects against gets left off.
"""

from __future__ import annotations

import unittest
from unittest import mock

from tradingagents.setup.settings import setting
from webui.components.platform_settings import (
    ARMING,
    confirmation_detail,
    setting_row,
    settings_body,
)


def text(component) -> str:
    if component is None:
        return ""
    if isinstance(component, str):
        return component
    if isinstance(component, (list, tuple)):
        return " ".join(text(item) for item in component)
    return text(getattr(component, "children", None))


def controls(component, found=None):
    found = [] if found is None else found
    identifier = getattr(component, "id", None)
    if isinstance(identifier, dict) and identifier.get("type") == "platform-setting":
        found.append(identifier["key"])
    children = getattr(component, "children", None)
    if isinstance(children, (list, tuple)):
        for child in children:
            controls(child, found)
    elif children is not None:
        controls(children, found)
    return found


class PanelTests(unittest.TestCase):
    def test_the_three_arming_switches_are_grouped_first(self):
        """Ordered by consequence, not alphabet."""
        rendered = controls(settings_body({}))

        self.assertEqual(rendered[: len(ARMING)], list(ARMING))

    def test_every_arming_switch_is_marked_as_one(self):
        for key in ARMING:
            with self.subTest(key=key):
                self.assertTrue(setting(key).dangerous)
                self.assertIn("arms the system", text(setting_row(setting(key), None)))

    def test_a_harmless_setting_carries_no_warning(self):
        self.assertNotIn(
            "arms the system", text(setting_row(setting("llm_provider"), "openai"))
        )

    def test_the_provider_settings_offer_the_registry(self):
        from tradingagents.setup.providers import MODEL_PROVIDERS

        def options(component):
            if type(component).__name__ == "Select":
                return [item["value"] for item in component.options]
            children = getattr(component, "children", None)
            if isinstance(children, (list, tuple)):
                for child in children:
                    found = options(child)
                    if found:
                        return found
            elif children is not None:
                return options(children)
            return []

        offered = options(setting_row(setting("llm_provider"), "openai"))

        self.assertEqual(set(offered), {item.id for item in MODEL_PROVIDERS})

    def test_the_panel_says_a_change_needs_no_redeploy(self):
        from webui.components.platform_settings import create_platform_settings

        self.assertIn("without a redeploy", text(create_platform_settings()))


class ConfirmationTests(unittest.TestCase):
    """What the operator is told before arming something."""

    def test_going_live_says_real_money(self):
        rendered = text(confirmation_detail(setting("alpaca_use_paper"), False))

        self.assertIn("Real money", rendered)

    def test_leaving_dry_run_says_orders_will_be_sent(self):
        rendered = text(confirmation_detail(setting("execution_gateway"), "broker"))

        self.assertIn("sent to the broker", rendered)

    def test_enabling_the_worker_says_nobody_is_watching(self):
        rendered = text(confirmation_detail(setting("autonomous_enabled"), True))

        self.assertIn("nobody watching", rendered)

    def test_it_reminds_you_the_other_switches_still_apply(self):
        """Three independent gates; arming one is not arming all."""
        rendered = text(confirmation_detail(setting("execution_gateway"), "broker"))

        self.assertIn("independently", rendered)


class AsymmetryTests(unittest.TestCase):
    """Safer is always one click; more dangerous never is."""

    def test_every_step_towards_safety_is_ungated(self):
        for key, safe in (
            ("autonomous_enabled", False),
            ("execution_gateway", "dry-run"),
            ("alpaca_use_paper", True),
        ):
            with self.subTest(key=key):
                self.assertFalse(setting(key).is_dangerous_change(safe))

    def test_every_step_away_from_it_is_gated(self):
        for key, unsafe in (
            ("autonomous_enabled", True),
            ("execution_gateway", "broker"),
            ("execution_gateway", "alpaca"),
            ("alpaca_use_paper", False),
        ):
            with self.subTest(key=key, value=unsafe):
                self.assertTrue(setting(key).is_dangerous_change(unsafe))


class CallbackTests(unittest.TestCase):
    def _register(self):
        from webui.callbacks import setup_callbacks

        captured = {}

        class App:
            def callback(self, *_args, **_kwargs):
                def decorate(function):
                    captured[function.__name__] = function
                    return function

                return decorate

        setup_callbacks.register_platform_callbacks(App())
        return captured

    def test_a_dangerous_change_opens_the_confirmation_instead_of_saving(self):
        captured = self._register()
        store = mock.Mock()

        with mock.patch(
            "tradingagents.setup.settings.get_runtime_settings", lambda: store
        ), mock.patch(
            "webui.callbacks.setup_callbacks.current_config",
            lambda: {"execution_gateway": "dry-run"},
        ), mock.patch(
            "webui.callbacks.setup_callbacks.ctx",
            mock.Mock(triggered_id={"type": "platform-setting",
                                     "key": "execution_gateway"}),
        ):
            is_open, detail, pending, _status = captured["changed"](
                ["broker"],
                [{"type": "platform-setting", "key": "execution_gateway"}],
            )

        self.assertTrue(is_open)
        self.assertEqual(pending, {"key": "execution_gateway", "value": "broker"})
        self.assertIn("sent to the broker", text(detail))
        store.set.assert_not_called()

    def test_a_safe_change_is_saved_without_asking(self):
        captured = self._register()
        store = mock.Mock()

        with mock.patch(
            "tradingagents.setup.settings.get_runtime_settings", lambda: store
        ), mock.patch(
            "webui.callbacks.setup_callbacks.current_config",
            lambda: {"llm_provider": "openai"},
        ), mock.patch(
            "webui.callbacks.setup_callbacks.ctx",
            mock.Mock(triggered_id={"type": "platform-setting",
                                     "key": "llm_provider"}),
        ):
            captured["changed"](
                ["anthropic"],
                [{"type": "platform-setting", "key": "llm_provider"}],
            )

        store.set.assert_called_once()
        self.assertEqual(store.set.call_args.args[:2], ("llm_provider", "anthropic"))

    def test_confirming_records_the_reason(self):
        captured = self._register()
        store = mock.Mock()

        with mock.patch(
            "tradingagents.setup.settings.get_runtime_settings", lambda: store
        ), mock.patch(
            "webui.callbacks.setup_callbacks.ctx",
            mock.Mock(triggered_id="platform-confirm-accept"),
        ):
            captured["confirm"](
                1, None,
                {"key": "execution_gateway", "value": "broker"},
                "paper looked right for two weeks",
            )

        self.assertEqual(
            store.set.call_args.kwargs["reason"], "paper looked right for two weeks"
        )

    def test_cancelling_changes_nothing(self):
        captured = self._register()
        store = mock.Mock()

        with mock.patch(
            "tradingagents.setup.settings.get_runtime_settings", lambda: store
        ), mock.patch(
            "webui.callbacks.setup_callbacks.ctx",
            mock.Mock(triggered_id="platform-confirm-cancel"),
        ):
            is_open, _status = captured["confirm"](
                None, 1, {"key": "execution_gateway", "value": "broker"}, ""
            )

        self.assertFalse(is_open)
        store.set.assert_not_called()

    def test_no_database_says_so_rather_than_failing_silently(self):
        captured = self._register()

        with mock.patch(
            "tradingagents.setup.settings.get_runtime_settings", lambda: None
        ), mock.patch(
            "webui.callbacks.setup_callbacks.current_config",
            lambda: {"llm_provider": "openai"},
        ), mock.patch(
            "webui.callbacks.setup_callbacks.ctx",
            mock.Mock(triggered_id={"type": "platform-setting",
                                     "key": "llm_provider"}),
        ):
            _open, _detail, _pending, status = captured["changed"](
                ["anthropic"],
                [{"type": "platform-setting", "key": "llm_provider"}],
            )

        self.assertIn("No database", text(status))


if __name__ == "__main__":
    unittest.main()
