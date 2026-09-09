"""Tests for preview, replay, and operator overrides.

The three things that turn the workbench from something you read into
something you operate. All were already supported by the backend; none had
a surface. What matters most here is that a preview reaches no broker and
leaves no lifecycle record, and that lifting a quarantine is recorded.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import dash

from conftest import dash_callback
from tradingagents.agents.schemas import (
    ExecutableAction,
    RiskDecision,
    build_trade_intent_from_risk_decision,
)
from tradingagents.execution.gates import GateLedger
from tradingagents.workbench.preview import (
    OVERRIDE_EVENT,
    PreviewUnavailable,
    compare_ledgers,
    preview_intent,
    preview_ledger,
    replay_tape,
    resume_scope,
)
from tradingagents.workbench.tape import build_tape
from webui.callbacks import override_callbacks

NOW = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)


def _intent():
    return build_trade_intent_from_risk_decision(
        symbol="NVDA",
        trading_mode="investment",
        current_position="NEUTRAL",
        decision=RiskDecision(
            action=ExecutableAction.BUY,
            confidence="high",
            risk_rationale="test",
            required_controls="stop at 95",
            target_portfolio_pct=1.0,
        ),
        trade_date="2026-09-09",
    )


def _ledger(*, blocked=None, clipped_to=None, requested=1_000.0):
    ledger = GateLedger(
        decision_id="decision-1", symbol="NVDA", requested_notional=requested
    )
    ledger.passed("intent")
    if clipped_to is not None:
        ledger.clipped(
            "risk_sizing", notional_before=requested, notional_after=clipped_to
        )
    if blocked:
        ledger.blocked(blocked, reasons=["a stated reason"])
    else:
        ledger.passed("submission")
    return ledger


def _summary(**overrides):
    fields = {
        "decision_id": "decision-1",
        "symbol": "NVDA",
        "status": "succeeded",
        "created_at": NOW,
        "updated_at": NOW,
        "broker": "alpaca",
        "order_count": 1,
        "filled_quantity": 10.0,
        "error": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _tape(*, ledger=None, intent=None):
    return build_tape(
        summary=_summary(),
        gate_ledger=ledger if ledger is not None else _ledger(),
        intent=intent if intent is not None else _intent().model_dump(mode="json"),
    )


class PreviewTests(unittest.TestCase):
    def _run(self, notional=1_000.0, intent=None, config_overrides=None):
        captured = {}

        def execute(symbol, parsed, requested, **kwargs):
            captured["symbol"] = symbol
            captured["requested"] = requested
            captured.update(kwargs)
            return {
                "success": True,
                "gate_ledger": _ledger(requested=requested).model_dump(mode="json"),
            }

        with mock.patch("tradingagents.dataflows.config.get_config", lambda: {}):
            result = preview_intent(
                intent or _intent(),
                notional,
                config_overrides=config_overrides,
                execute=execute,
            )
        return result, captured

    def test_a_preview_reaches_the_dry_run_gateway_only(self):
        """The whole point: every gate is evaluated, nothing is sent."""
        from tradingagents.execution.dry_run_gateway import DryRunExecutionGateway

        _result, captured = self._run()

        self.assertIsInstance(captured["gateway"], DryRunExecutionGateway)

    def test_a_preview_leaves_no_lifecycle_record(self):
        """A record here would collide with a later real attempt."""
        _result, captured = self._run()

        self.assertIsNone(captured["lifecycle"])
        with self.assertRaises(AssertionError):
            captured["unit_of_work_factory"]()

    def test_the_result_is_marked_as_a_preview(self):
        result, _captured = self._run()

        self.assertTrue(result["preview"])

    def test_the_size_under_test_reaches_the_pipeline(self):
        _result, captured = self._run(notional=2_500.0)

        self.assertEqual(captured["requested"], 2_500.0)

    def test_a_dict_intent_is_accepted(self):
        _result, captured = self._run(intent=_intent().model_dump(mode="json"))

        self.assertEqual(captured["symbol"], "NVDA")

    def test_a_malformed_intent_is_refused_before_anything_runs(self):
        with self.assertRaises(PreviewUnavailable):
            preview_intent({"not": "an intent"}, 1_000.0, execute=lambda *a, **k: {})

    def test_the_ledger_is_readable_out_of_the_result(self):
        result, _captured = self._run()

        self.assertIsNotNone(preview_ledger(result))

    def test_a_result_with_no_ledger_yields_none(self):
        self.assertIsNone(preview_ledger({"success": False}))


class ReplayTests(unittest.TestCase):
    def _replay(self, tape, **kwargs):
        captured = {}

        def execute(symbol, parsed, requested, **options):
            captured["requested"] = requested
            return {"success": True, "gate_ledger": _ledger().model_dump(mode="json")}

        with mock.patch("tradingagents.dataflows.config.get_config", lambda: {}):
            result = replay_tape(tape, execute=execute, **kwargs)
        return result, captured

    def test_a_replay_reuses_the_original_size_by_default(self):
        _result, captured = self._replay(_tape(ledger=_ledger(requested=2_000.0)))

        self.assertEqual(captured["requested"], 2_000.0)

    def test_a_replay_can_be_resized(self):
        _result, captured = self._replay(_tape(), requested_notional=500.0)

        self.assertEqual(captured["requested"], 500.0)

    def test_a_decision_with_no_recorded_intent_cannot_be_replayed(self):
        """Better to say so than to invent one."""
        with self.assertRaises(PreviewUnavailable):
            replay_tape(_tape(intent={}))

    def test_a_decision_with_no_ledger_replays_at_nothing(self):
        tape = build_tape(
            summary=_summary(), intent=_intent().model_dump(mode="json")
        )

        _result, captured = self._replay(tape)

        self.assertEqual(captured["requested"], 0.0)


class ComparisonTests(unittest.TestCase):
    def test_every_gate_in_the_candidate_is_reported(self):
        rows = compare_ledgers(_ledger(), _ledger())

        self.assertEqual([row["gate"] for row in rows], ["intent", "submission"])

    def test_an_unchanged_gate_is_marked_unchanged(self):
        rows = compare_ledgers(_ledger(), _ledger())

        self.assertTrue(all(not row["changed"] for row in rows))

    def test_a_gate_that_now_blocks_is_marked_changed(self):
        rows = compare_ledgers(_ledger(), _ledger(blocked="safety"))

        safety = next(row for row in rows if row["gate"] == "safety")
        self.assertTrue(safety["changed"])
        self.assertIsNone(safety["was"])
        self.assertEqual(safety["now"], "blocked")

    def test_a_gate_that_changed_verdict_is_marked_changed(self):
        original = GateLedger(
            decision_id="d", symbol="NVDA", requested_notional=1_000.0
        )
        original.passed("risk_sizing")
        candidate = GateLedger(
            decision_id="d", symbol="NVDA", requested_notional=1_000.0
        )
        candidate.clipped(
            "risk_sizing", notional_before=1_000.0, notional_after=400.0
        )

        rows = compare_ledgers(original, candidate)

        self.assertTrue(rows[0]["changed"])
        self.assertEqual(rows[0]["was"], "passed")
        self.assertEqual(rows[0]["now"], "clipped")

    def test_a_decision_with_no_original_ledger_still_compares(self):
        rows = compare_ledgers(None, _ledger())

        self.assertTrue(all(row["was"] is None for row in rows))

    def test_no_candidate_compares_to_nothing(self):
        self.assertEqual(compare_ledgers(_ledger(), None), [])


class ResumeTests(unittest.TestCase):
    def _factory(self, error=None):
        appended = []
        operations = mock.MagicMock()
        operations.set_paused.return_value = SimpleNamespace(
            service="execution:alpaca", paused=False
        )
        if error:
            operations.set_paused.side_effect = error

        class Journal:
            def append(self, event_type, **kwargs):
                appended.append((event_type, kwargs))

        uow = mock.MagicMock()
        uow.__enter__ = lambda _self: SimpleNamespace(
            operations=operations, journal=Journal(), commit=lambda: None
        )
        uow.__exit__ = lambda *a: False
        return (lambda: uow), operations, appended

    def test_resuming_clears_the_pause(self):
        factory, operations, _appended = self._factory()

        outcome = resume_scope(factory, "execution:alpaca", actor="alice")

        operations.set_paused.assert_called_once_with(
            "execution:alpaca", paused=False, reason=None, updated_by="alice"
        )
        self.assertFalse(outcome["paused"])

    def test_the_override_is_recorded_with_who_and_why(self):
        """Lifting a quarantine is a decision in its own right."""
        factory, _operations, appended = self._factory()

        resume_scope(
            factory, "execution:alpaca", actor="alice", reason="drift reconciled"
        )

        event_type, kwargs = appended[0]
        self.assertEqual(event_type, OVERRIDE_EVENT)
        self.assertEqual(kwargs["payload"]["actor"], "alice")
        self.assertEqual(kwargs["payload"]["reason"], "drift reconciled")
        self.assertEqual(kwargs["payload"]["scope"], "execution:alpaca")

    def test_a_scope_is_required(self):
        factory, _operations, _appended = self._factory()

        with self.assertRaises(ValueError):
            resume_scope(factory, "  ", actor="alice")

    def test_an_actor_is_required(self):
        """An unattributed override is not an override."""
        factory, _operations, _appended = self._factory()

        with self.assertRaises(ValueError):
            resume_scope(factory, "execution:alpaca", actor="")

    def test_a_failure_propagates_rather_than_reporting_success(self):
        factory, _operations, _appended = self._factory(RuntimeError("database gone"))

        with self.assertRaises(RuntimeError):
            resume_scope(factory, "execution:alpaca", actor="alice")


class OverrideCallbackTests(unittest.TestCase):
    def setUp(self):
        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        override_callbacks.register_override_callbacks(app)
        self.app = app

    def _runtime(self, *, postgres=True, error=None):
        if not postgres:
            runtime = SimpleNamespace(unit_of_work_factory=None)
        else:
            operations = mock.MagicMock()
            operations.set_paused.return_value = SimpleNamespace(
                service="execution:alpaca", paused=False
            )
            if error:
                operations.set_paused.side_effect = error
            uow = mock.MagicMock()
            uow.__enter__ = lambda _self: SimpleNamespace(
                operations=operations,
                journal=SimpleNamespace(append=lambda *a, **k: None),
                commit=lambda: None,
            )
            uow.__exit__ = lambda *a: False
            runtime = SimpleNamespace(unit_of_work_factory=lambda: uow)
        patcher = mock.patch.object(
            override_callbacks, "get_persistence_runtime", lambda: runtime
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _resume(self, clicks, scope="execution:alpaca"):
        with mock.patch.object(
            override_callbacks,
            "ctx",
            SimpleNamespace(triggered_id={"type": "resume-scope", "scope": scope}),
        ):
            return dash_callback(self.app, "vitals-override-result.children")(clicks)

    def test_a_paused_scope_offers_a_resume_control_with_its_reason(self):
        controls = [
            SimpleNamespace(
                service="execution:alpaca", paused=True, reason="account drift"
            )
        ]

        rendered = str(override_callbacks.render_overrides(controls))

        self.assertIn("execution:alpaca", rendered)
        self.assertIn("account drift", rendered)
        self.assertIn("recorded against your user", rendered)

    def test_nothing_paused_offers_nothing(self):
        self.assertNotIn("Resume", str(override_callbacks.render_overrides([])))

    def test_resuming_reports_that_the_override_was_recorded(self):
        self._runtime()

        rendered = str(self._resume([1]))

        self.assertIn("Resumed execution:alpaca", rendered)
        self.assertIn("recorded", rendered)

    def test_no_click_does_nothing(self):
        self._runtime()

        self.assertEqual(self._resume([0]), "")
        self.assertEqual(self._resume(None), "")

    def test_a_failed_resume_reports_the_reason(self):
        self._runtime(error=RuntimeError("database gone"))

        rendered = str(self._resume([1]))

        self.assertIn("Could not resume", rendered)
        self.assertIn("database gone", rendered)

    def test_without_postgres_overrides_say_so(self):
        self._runtime(postgres=False)

        self.assertIn("require PostgreSQL", str(self._resume([1])))

    def test_a_click_with_no_scope_does_nothing(self):
        self._runtime()

        with mock.patch.object(
            override_callbacks, "ctx", SimpleNamespace(triggered_id=None)
        ):
            result = dash_callback(self.app, "vitals-override-result.children")([1])

        self.assertEqual(result, "")


class PreviewCallbackTests(unittest.TestCase):
    def setUp(self):
        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        override_callbacks.register_override_callbacks(app)
        self.app = app

    def _preview(self, clicks=1, decision_id="decision-1", notional=None,
                 tape=None, replay=None, load_error=None):
        def load(_decision_id):
            if load_error:
                raise load_error
            return tape

        patchers = [
            mock.patch.object(override_callbacks, "load_tape", load),
        ]
        if replay is not None:
            patchers.append(mock.patch.object(override_callbacks, "replay_tape", replay))
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        return dash_callback(self.app, "workbench-preview-result.children")(
            clicks, decision_id, notional
        )

    def test_a_preview_renders_the_hypothetical_ledger_and_its_waterfall(self):
        body, figure = self._preview(
            tape=_tape(),
            replay=lambda tape, **kwargs: {
                "success": True,
                "gate_ledger": _ledger().model_dump(mode="json"),
            },
        )

        self.assertIn("Cleared", str(body))
        self.assertEqual(list(figure.data[0].x)[0], "Requested")

    def test_a_preview_says_which_gates_would_decide_differently(self):
        body, _figure = self._preview(
            tape=_tape(),
            replay=lambda tape, **kwargs: {
                "success": False,
                "gate_ledger": _ledger(blocked="safety").model_dump(mode="json"),
            },
        )

        rendered = str(body)
        self.assertIn("would decide differently", rendered)
        self.assertIn("Safety limits", rendered)

    def test_an_unchanged_preview_says_so(self):
        body, _figure = self._preview(
            tape=_tape(),
            replay=lambda tape, **kwargs: {
                "success": True,
                "gate_ledger": _ledger().model_dump(mode="json"),
            },
        )

        self.assertIn("same way", str(body))

    def test_the_requested_size_reaches_the_replay(self):
        captured = {}

        def replay(tape, requested_notional=None, **kwargs):
            captured["requested"] = requested_notional
            return {"success": True, "gate_ledger": _ledger().model_dump(mode="json")}

        self._preview(tape=_tape(), notional=750, replay=replay)

        self.assertEqual(captured["requested"], 750.0)

    def test_a_decision_that_cannot_be_replayed_says_why(self):
        def replay(tape, **kwargs):
            raise PreviewUnavailable("The original trade intent was not recorded")

        body, _figure = self._preview(tape=_tape(), replay=replay)

        self.assertIn("not recorded", str(body))

    def test_a_failing_preview_reports_the_reason(self):
        def replay(tape, **kwargs):
            raise RuntimeError("broker unreachable")

        body, _figure = self._preview(tape=_tape(), replay=replay)

        self.assertIn("Preview failed", str(body))
        self.assertIn("broker unreachable", str(body))

    def test_a_failing_lookup_reports_the_reason(self):
        body, _figure = self._preview(load_error=RuntimeError("connection reset"))

        self.assertIn("Unable to load decision", str(body))

    def test_without_postgres_previews_say_so(self):
        body, _figure = self._preview(tape=None)

        self.assertIn("require PostgreSQL", str(body))

    def test_no_click_or_no_selection_does_nothing(self):
        self.assertEqual(self._preview(clicks=0, tape=_tape())[0], "")
        self.assertEqual(self._preview(decision_id=None, tape=_tape())[0], "")

    def test_a_preview_with_no_ledger_says_so_rather_than_charting_nothing(self):
        body, _figure = self._preview(
            tape=_tape(),
            replay=lambda tape, **kwargs: {"success": False, "error": "refused early"},
        )

        self.assertIn("refused early", str(body))


if __name__ == "__main__":
    unittest.main()
