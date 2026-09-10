"""Tests for the guided setup.

The wizard's whole claim is that it asks for less than the Integrations
screen does, so most of these are about what it does *not* ask. The rest
are about the two ways a stepped flow goes wrong: skipping a step, and
destroying something you already had.
"""

from __future__ import annotations

import unittest
from unittest import mock

from tradingagents.setup import evaluate_readiness
from webui.callbacks.setup_callbacks import build_plan
from webui.components.setup_wizard import (
    POSTURE_STEP,
    WELCOME_STEP,
    posture_step,
    requirement_step,
    welcome_step,
)

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
    """Everything rendered, flattened, so assertions can be about wording."""
    if component is None:
        return ""
    if isinstance(component, str):
        return component
    if isinstance(component, (list, tuple)):
        return " ".join(text(item) for item in component)
    return text(getattr(component, "children", None))


class PlanTests(unittest.TestCase):
    def test_it_opens_and_closes_with_the_same_two_bookends(self):
        plan = build_plan(readiness())

        self.assertEqual(plan[0], WELCOME_STEP)
        self.assertEqual(plan[-1], POSTURE_STEP)

    def test_a_default_install_is_asked_for_three_things(self):
        """A model, a broker, and one market-data key."""
        plan = build_plan(readiness())

        self.assertEqual(
            [step for step in plan if step not in (WELCOME_STEP, POSTURE_STEP)],
            ["model", "broker", "equity_news"],
        )

    def test_configured_credentials_drop_out_of_the_plan(self):
        plan = build_plan(
            readiness(configured=["openai_api_key", "alpaca_api_key", "alpaca_secret_key"])
        )

        self.assertNotIn("model", plan)
        self.assertNotIn("broker", plan)

    def test_it_never_asks_for_a_key_a_disabled_analyst_would_read(self):
        self.assertNotIn("macro", build_plan(readiness()))

    def test_turning_the_macro_analyst_on_adds_the_step(self):
        plan = build_plan(readiness({"autonomous_analysts": "market,macro"}))

        self.assertIn("macro", plan)

    def test_it_never_asks_for_a_plainly_optional_key(self):
        self.assertNotIn("fallback_market_data", build_plan(readiness()))

    def test_a_finished_deployment_is_just_the_bookends(self):
        plan = build_plan(
            readiness(
                configured=[
                    "openai_api_key", "alpaca_api_key", "alpaca_secret_key",
                    "finnhub_api_key",
                ]
            )
        )

        self.assertEqual(plan, [WELCOME_STEP, POSTURE_STEP])

    def test_an_unreadable_configuration_still_produces_a_walkable_plan(self):
        plan = build_plan(None)

        self.assertEqual(plan, [WELCOME_STEP, POSTURE_STEP])


class StepStabilityTests(unittest.TestCase):
    """Why the plan is stored rather than recomputed.

    Recomputing on every render drops the step just completed. The index
    has already advanced past it, so the *next* requirement lands where the
    completed one was and is never shown — you finish the wizard having
    silently skipped one.
    """

    def test_recomputing_would_have_skipped_a_step(self):
        before = build_plan(readiness())
        after = build_plan(readiness(configured=["openai_api_key"]))

        # Standing on step 1 ("model") and pressing Next moves to index 2.
        self.assertEqual(before[1], "model")
        self.assertEqual(before[2], "broker")
        # Recomputed, index 2 is no longer the broker.
        self.assertNotEqual(after[2], "broker")


class WelcomeTests(unittest.TestCase):
    def test_it_says_how_many_rather_than_showing_sixteen(self):
        view = readiness()
        steps = [view.get(item) for item in build_plan(view)[1:-1]]

        rendered = text(welcome_step(view, steps))

        self.assertIn("3 things", rendered)

    def test_it_explains_why_the_integrations_list_is_longer(self):
        view = readiness()
        steps = [view.get(item) for item in build_plan(view)[1:-1]]

        self.assertIn("not every provider you need", text(welcome_step(view, steps)))

    def test_it_promises_nothing_can_trade(self):
        view = readiness()
        steps = [view.get(item) for item in build_plan(view)[1:-1]]

        self.assertIn(
            "Nothing configured here can place an order",
            text(welcome_step(view, steps)),
        )

    def test_a_finished_deployment_gets_told_so(self):
        self.assertIn("Nothing left to set up", text(welcome_step(readiness(), [])))


class RequirementStepTests(unittest.TestCase):
    def test_it_says_what_the_credential_buys(self):
        view = readiness()

        rendered = text(requirement_step(view.get("equity_news")))

        self.assertIn("Google News", rendered)

    def test_a_conditional_step_names_its_condition(self):
        view = readiness({"autonomous_analysts": "market,macro"})

        rendered = text(requirement_step(view.get("macro")))

        self.assertIn("macro analyst", rendered)

    def test_an_already_set_credential_offers_to_keep_it(self):
        """Blank means leave it alone, and the box has to say so, or the
        obvious reading is that tabbing past will clear it."""
        view = readiness(configured=["finnhub_api_key"])

        rendered = requirement_step(view.get("equity_news"))

        self.assertIn("already set", text(rendered))

    def test_every_input_is_a_password_field(self):
        def inputs(component, found=None):
            found = [] if found is None else found
            if type(component).__name__ == "Input":
                found.append(component)
            children = getattr(component, "children", None)
            if isinstance(children, (list, tuple)):
                for child in children:
                    inputs(child, found)
            elif children is not None:
                inputs(children, found)
            return found

        for step in ("model", "broker", "equity_news"):
            view = readiness()
            for field in inputs(requirement_step(view.get(step))):
                with self.subTest(step=step):
                    self.assertEqual(field.type, "password")


class PostureTests(unittest.TestCase):
    """The last screen answers "can this place an order?" out loud."""

    def test_the_safe_default_reads_as_safe(self):
        rendered = text(
            posture_step(
                {
                    "autonomous_enabled": False,
                    "execution_gateway": "dry-run",
                    "alpaca_use_paper": True,
                }
            )
        )

        self.assertIn("disabled", rendered)
        self.assertIn("dry-run", rendered)
        self.assertIn("paper", rendered)

    def test_a_live_account_is_named_in_capitals(self):
        rendered = text(
            posture_step(
                {
                    "autonomous_enabled": True,
                    "execution_gateway": "broker",
                    "alpaca_use_paper": False,
                }
            )
        )

        self.assertIn("LIVE", rendered)

    def test_it_names_the_environment_variable_behind_each_switch(self):
        rendered = text(posture_step({}))

        for variable in (
            "AUTONOMOUS_ENABLED", "EXECUTION_GATEWAY", "ALPACA_USE_PAPER"
        ):
            with self.subTest(variable=variable):
                self.assertIn(variable, rendered)

    def test_it_offers_no_control_to_arm_anything(self):
        """Stated, not switchable. A setup wizard is the wrong place to
        arm a trading system."""

        def has_control(component):
            if type(component).__name__ in ("Switch", "Checkbox", "Button", "Select"):
                return True
            children = getattr(component, "children", None)
            if isinstance(children, (list, tuple)):
                return any(has_control(child) for child in children)
            if children is not None:
                return has_control(children)
            return False

        self.assertFalse(has_control(posture_step({})))


class SaveTests(unittest.TestCase):
    """Nothing is written when nothing was typed."""

    def test_a_missing_vault_is_reported_rather_than_swallowed(self):
        from webui.callbacks import setup_callbacks

        captured = {}

        class App:
            def callback(self, *_args, **_kwargs):
                def decorate(function):
                    captured[function.__name__] = function
                    return function

                return decorate

        setup_callbacks.register_setup_callbacks(App())

        with mock.patch(
            "tradingagents.integrations.get_integration_vault", lambda: None
        ):
            result = captured["save"](
                1, ["a-key"], [{"type": "wizard-credential", "key": "openai_api_key"}]
            )

        self.assertIn("Nothing was saved", text(result))

    def test_blank_fields_write_nothing_at_all(self):
        from dash import no_update
        from webui.callbacks import setup_callbacks

        captured = {}

        class App:
            def callback(self, *_args, **_kwargs):
                def decorate(function):
                    captured[function.__name__] = function
                    return function

                return decorate

        setup_callbacks.register_setup_callbacks(App())

        vault = mock.Mock()
        with mock.patch(
            "tradingagents.integrations.get_integration_vault", lambda: vault
        ):
            result = captured["save"](
                1, ["", "   "],
                [
                    {"type": "wizard-credential", "key": "alpaca_api_key"},
                    {"type": "wizard-credential", "key": "alpaca_secret_key"},
                ],
            )

        self.assertIs(result, no_update)
        vault.set_many.assert_not_called()

    def test_what_was_typed_reaches_the_vault(self):
        from webui.callbacks import setup_callbacks

        captured = {}

        class App:
            def callback(self, *_args, **_kwargs):
                def decorate(function):
                    captured[function.__name__] = function
                    return function

                return decorate

        setup_callbacks.register_setup_callbacks(App())

        vault = mock.Mock()
        vault.set_many.return_value = 1
        with mock.patch(
            "tradingagents.integrations.get_integration_vault", lambda: vault
        ):
            captured["save"](
                1, [" sk-test "],
                [{"type": "wizard-credential", "key": "openai_api_key"}],
            )

        vault.set_many.assert_called_once()
        self.assertEqual(
            vault.set_many.call_args.args[0], {"openai_api_key": "sk-test"}
        )


if __name__ == "__main__":
    unittest.main()
