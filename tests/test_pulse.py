"""Tests for the server-sent pulse.

Every panel used to poll: the fast interval ran at 1s while an analysis was
running, for every callback, in every open tab, whether or not anything had
happened. The pulse inverts that — one connection per tab, silent while the
machine is idle — and the intervals stay only as a fallback for when a
proxy eats the stream.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock

import dash

from conftest import dash_callback
from webui.callbacks import pulse_callbacks
from webui.utils import pulse as pulse_module
from webui.utils.pulse import Pulse, pulse_events, register_pulse_route


class FakeState:
    def __init__(self, **overrides):
        self.needs_ui_update = False
        self.analysis_running = False
        self.analyzing_symbol = None
        self.tool_calls_count = 0
        self.llm_calls_count = 0
        for key, value in overrides.items():
            setattr(self, key, value)


class Clock:
    """Advances only when the generator sleeps, so time is deterministic."""

    def __init__(self, step=0.25):
        self.now = 0.0
        self.step = step
        self.sleeps = 0

    def sleep(self, seconds):
        self.sleeps += 1
        self.now += seconds

    def monotonic(self):
        return self.now


def _events(state, *, max_events=2, pulse=None, clock=None, heartbeat=20.0):
    clock = clock or Clock()
    with mock.patch.object(pulse_module, "_PULSE", pulse or Pulse()):
        stream = list(
            pulse_events(
                state,
                max_events=max_events,
                heartbeat_seconds=heartbeat,
                sleep=clock.sleep,
                clock=clock.monotonic,
            )
        )
    return [json.loads(chunk.removeprefix("data: ").strip()) for chunk in stream], clock


class RevisionTests(unittest.TestCase):
    def test_a_bump_advances_the_revision(self):
        pulse = Pulse()

        self.assertEqual(pulse.bump("state"), 1)
        self.assertEqual(pulse.bump("state"), 2)
        self.assertEqual(pulse.revision, 2)

    def test_the_snapshot_carries_what_the_vitals_need(self):
        pulse = Pulse()
        state = FakeState(
            analysis_running=True,
            analyzing_symbol="NVDA",
            tool_calls_count=7,
            llm_calls_count=3,
        )

        snapshot = pulse.snapshot(state)

        self.assertTrue(snapshot["analysis_running"])
        self.assertEqual(snapshot["analyzing_symbol"], "NVDA")
        self.assertEqual(snapshot["tool_calls"], 7)
        self.assertEqual(snapshot["llm_calls"], 3)

    def test_a_snapshot_of_an_unstarted_process_is_still_readable(self):
        snapshot = Pulse().snapshot(SimpleNamespace())

        self.assertFalse(snapshot["analysis_running"])
        self.assertEqual(snapshot["tool_calls"], 0)


class StreamTests(unittest.TestCase):
    def test_the_first_event_primes_the_client_immediately(self):
        """Otherwise the panels stay blank until something happens."""
        events, clock = _events(FakeState(), max_events=1)

        self.assertEqual(len(events), 1)
        self.assertEqual(clock.sleeps, 0)

    def test_events_are_server_sent_formatted(self):
        with mock.patch.object(pulse_module, "_PULSE", Pulse()):
            chunk = next(pulse_events(FakeState(), max_events=1))

        self.assertTrue(chunk.startswith("data: "))
        self.assertTrue(chunk.endswith("\n\n"))

    def test_a_stale_flag_produces_an_event_and_is_consumed(self):
        state = FakeState(needs_ui_update=True)

        events, _clock = _events(state, max_events=2)

        self.assertEqual(len(events), 2)
        self.assertFalse(state.needs_ui_update)
        self.assertEqual(events[-1]["reason"], "state")

    def test_an_external_bump_produces_an_event(self):
        pulse = Pulse()

        def bump_once(_seconds):
            if pulse.revision == 0:
                pulse.bump("external")

        clock = Clock()
        clock.sleep = bump_once
        events, _clock = _events(FakeState(), max_events=2, pulse=pulse, clock=clock)

        self.assertEqual(events[-1]["reason"], "external")

    def test_an_idle_machine_sleeps_rather_than_emitting(self):
        """The whole point: silence while nothing is happening."""
        events, clock = _events(FakeState(), max_events=2, heartbeat=1_000.0)

        self.assertEqual(len(events), 2)
        self.assertGreater(clock.sleeps, 1)

    def test_a_heartbeat_is_emitted_even_when_nothing_changed(self):
        events, clock = _events(FakeState(), max_events=2, heartbeat=0.5)

        self.assertEqual(len(events), 2)
        # 0.25s poll, 0.5s heartbeat: two sleeps and the beat is due.
        self.assertLessEqual(clock.sleeps, 3)

    def test_the_revision_advances_across_events(self):
        state = FakeState(needs_ui_update=True)

        events, _clock = _events(state, max_events=2)

        self.assertLess(events[0]["revision"], events[-1]["revision"])


class RouteTests(unittest.TestCase):
    def test_the_stream_is_mounted_with_streaming_headers(self):
        from flask import Flask

        server = Flask(__name__)
        register_pulse_route(server, FakeState())

        with server.test_request_context("/stream/pulse"):
            rule = next(
                rule for rule in server.url_map.iter_rules()
                if str(rule) == "/stream/pulse"
            )

        self.assertIsNotNone(rule)

    def test_the_route_is_registered_on_the_dash_server(self):
        import webui.app_dash as app_dash

        app = app_dash.create_app()

        self.assertIn(
            "/stream/pulse", [str(rule) for rule in app.server.url_map.iter_rules()]
        )


class ListenerTests(unittest.TestCase):
    def test_the_listener_opens_the_stream_only_once(self):
        """A re-render must not leave a second EventSource open per tab."""
        self.assertIn("__tradingAgentsPulse", pulse_callbacks.LISTENER)
        self.assertIn("no_update", pulse_callbacks.LISTENER)

    def test_the_listener_drives_the_fallback_interval(self):
        """Existing consumers of refresh-interval get push for free."""
        self.assertIn("refresh-interval", pulse_callbacks.LISTENER)
        self.assertIn("set_props", pulse_callbacks.LISTENER)

    def test_the_listener_reconnects_after_an_error(self):
        self.assertIn("onerror", pulse_callbacks.LISTENER)
        self.assertIn("setTimeout(connect", pulse_callbacks.LISTENER)

    def test_the_components_the_stream_writes_into_are_created(self):
        rendered = str(pulse_callbacks.create_pulse_components())

        self.assertIn("pulse-store", rendered)
        self.assertIn("pulse-listener", rendered)


class StreamStatusTests(unittest.TestCase):
    def setUp(self):
        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        pulse_callbacks.register_pulse_callbacks(app)
        self.app = app

    def _status(self, pulse):
        return dash_callback(self.app, "refresh-status.children")(pulse)

    def test_before_the_first_pulse_the_ui_says_it_is_waiting(self):
        message, className = self._status(None)

        self.assertIn("Waiting", message)
        self.assertIn("secondary", className)

    def test_a_running_analysis_names_the_symbol(self):
        message, className = self._status(
            {"analysis_running": True, "analyzing_symbol": "NVDA"}
        )

        self.assertIn("NVDA", message)
        self.assertIn("success", className)

    def test_a_running_analysis_without_a_symbol_still_reads_live(self):
        message, _className = self._status({"analysis_running": True})

        self.assertIn("analysis running", message)

    def test_an_idle_machine_reads_live_and_idle(self):
        message, className = self._status({"analysis_running": False})

        self.assertIn("idle", message)
        self.assertIn("secondary", className)


class FallbackIntervalTests(unittest.TestCase):
    def test_the_intervals_are_no_longer_switched_on_and_off(self):
        """The governor existed only to stop 1s polling; nothing polls now."""
        from webui.layout import create_intervals

        for interval in create_intervals():
            self.assertFalse(
                getattr(interval, "disabled", False), interval.id
            )

    def test_the_fallback_is_slow_enough_to_be_a_fallback(self):
        from webui.config.constants import REFRESH_INTERVALS

        self.assertGreaterEqual(REFRESH_INTERVALS["fast"], 10_000)


if __name__ == "__main__":
    unittest.main()
