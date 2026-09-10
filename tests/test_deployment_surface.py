"""Tests for the parts of the app a container runtime talks to.

Three small surfaces, each of which fails in a way that is quiet rather than
loud when it is wrong: the liveness endpoint the healthcheck polls, the port
policy that decides whether a clash is fatal, and the config key that stands
the hosted web search down.

Quiet failure is the theme. A healthcheck pointed at a missing route restarts
a healthy container forever; a web process that moves to a different port than
the one the pod published reports a clean start and serves nobody; a config
key with no way to set it looks like a feature and is not one.
"""

from __future__ import annotations

import importlib
import socket
import unittest
from unittest import mock

import run_webui_dash
from webui.utils.health import HEALTH_PATH, register_health_route


class HealthRouteTests(unittest.TestCase):
    """The endpoint the container healthcheck polls."""

    def _client(self):
        from flask import Flask

        server = Flask(__name__)
        register_health_route(server)
        return server.test_client()

    def test_it_answers_200(self):
        response = self._client().get(HEALTH_PATH)

        self.assertEqual(response.status_code, 200)

    def test_it_answers_in_plain_text(self):
        """The probe reads one byte; HTML would be a lie about what this is."""
        response = self._client().get(HEALTH_PATH)

        self.assertIn("text/plain", response.headers["Content-Type"])
        self.assertEqual(response.get_data(as_text=True).strip(), "ok")

    def test_the_path_is_the_one_the_image_polls(self):
        """The Containerfile hard-codes this; a rename has to break here."""
        self.assertEqual(HEALTH_PATH, "/healthz")

    def test_the_real_app_serves_it(self):
        """Registered by create_app, not only available to be registered."""
        from webui.app_dash import create_app

        response = create_app().server.test_client().get(HEALTH_PATH)

        self.assertEqual(response.status_code, 200)

    def test_it_does_not_touch_the_database(self):
        """Liveness, not readiness — see webui/utils/health.py.

        A probe that checked PostgreSQL would restart the web UI every time
        PostgreSQL blipped, and the web UI is where an operator goes to find
        out that PostgreSQL has blipped.
        """
        with mock.patch(
            "tradingagents.persistence.build_persistence_runtime",
            side_effect=AssertionError("the probe reached persistence"),
        ):
            response = self._client().get(HEALTH_PATH)

        self.assertEqual(response.status_code, 200)


class StrictPortTests(unittest.TestCase):
    """Whether a port clash is fatal or merely inconvenient."""

    def test_it_is_off_by_default(self):
        """A laptop should keep hunting; the console prints where it landed."""
        self.assertFalse(run_webui_dash.strict_port_requested({}))

    def test_the_image_turns_it_on(self):
        for value in ("1", "true", "TRUE", "yes", "on", " on "):
            self.assertTrue(
                run_webui_dash.strict_port_requested(
                    {run_webui_dash.STRICT_PORT_ENV: value}
                ),
                value,
            )

    def test_anything_else_leaves_it_off(self):
        for value in ("0", "false", "no", "", "maybe"):
            self.assertFalse(
                run_webui_dash.strict_port_requested(
                    {run_webui_dash.STRICT_PORT_ENV: value}
                ),
                value,
            )

    def test_a_free_port_reads_as_free(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        self.assertTrue(run_webui_dash.port_is_free(port, "127.0.0.1"))

    def test_a_taken_port_reads_as_taken(self):
        with socket.socket() as held:
            held.bind(("127.0.0.1", 0))
            held.listen(1)
            port = held.getsockname()[1]

            self.assertFalse(run_webui_dash.port_is_free(port, "127.0.0.1"))


class MainPortPolicyTests(unittest.TestCase):
    """What main() does with a clash, under each policy."""

    def _run_main(self, *, strict, port, free):
        args = mock.Mock(
            port=port, share=False, server_name="127.0.0.1", debug=False,
            max_threads=40,
        )
        started = {}

        def fake_run_app(**kwargs):
            started.update(kwargs)
            return 0

        with mock.patch.object(run_webui_dash, "parse_args", lambda: args), \
             mock.patch.object(run_webui_dash, "run_app", fake_run_app), \
             mock.patch(
                 "tradingagents.dataflows.virtual_stops_manager"
                 ".VirtualStopsManager.start_realtime_daemon",
                 lambda: {"started": False, "reason": "disabled"},
             ), \
             mock.patch.object(
                 run_webui_dash, "strict_port_requested", lambda env=None: strict
             ), \
             mock.patch.object(run_webui_dash, "port_is_free", lambda *a, **k: free), \
             mock.patch.object(
                 run_webui_dash,
                 "find_available_port",
                 lambda start, end=None: start if free else start + 1,
             ), \
             mock.patch.object(run_webui_dash.sys, "exit", lambda code=0: code):
            result = run_webui_dash.main()
        return result, started

    def test_a_clash_is_fatal_under_the_strict_policy(self):
        """The published port is the only one that reaches anybody, so
        landing on a different one is worse than not starting."""
        result, started = self._run_main(strict=True, port=7860, free=False)

        self.assertEqual(result, 1)
        self.assertEqual(started, {})

    def test_a_free_port_is_used_as_asked_under_the_strict_policy(self):
        _result, started = self._run_main(strict=True, port=7860, free=True)

        self.assertEqual(started["port"], 7860)

    def test_a_clash_moves_on_when_not_strict(self):
        _result, started = self._run_main(strict=False, port=7860, free=False)

        self.assertEqual(started["port"], 7861)


class PointInTimeConfigTests(unittest.TestCase):
    """The search gate's setting has to be reachable from a deployment.

    `require_point_in_time_web_search` shipped with the gate that reads it but
    with nothing that sets it, which is a feature that looks present and is
    not. These tests are about the wiring, not the gate.
    """

    ENV = "REQUIRE_POINT_IN_TIME_WEB_SEARCH"

    def _config_under(self, value):
        import os

        env = dict(os.environ)
        env[self.ENV] = value
        with mock.patch.dict("os.environ", env, clear=True):
            module = importlib.reload(
                importlib.import_module("tradingagents.default_config")
            )
            return dict(module.DEFAULT_CONFIG)

    def tearDown(self):
        importlib.reload(importlib.import_module("tradingagents.default_config"))

    def test_the_key_the_gate_reads_is_in_the_default_config(self):
        from tradingagents.dataflows.search_window import FORCE_CONFIG_KEY
        from tradingagents.default_config import DEFAULT_CONFIG

        self.assertIn(FORCE_CONFIG_KEY, DEFAULT_CONFIG)

    def test_an_unset_environment_leaves_live_search_available(self):
        from tradingagents.dataflows.search_window import (
            FORCE_CONFIG_KEY,
            live_search_allowed,
        )

        config = self._config_under("")

        # A future date is never historical, so only the force flag can
        # stand the search down here.
        self.assertTrue(live_search_allowed("2999-01-01", config=config))
        self.assertFalse(_truthy(config[FORCE_CONFIG_KEY]))

    def test_the_environment_stands_the_search_down(self):
        from tradingagents.dataflows.search_window import live_search_allowed

        config = self._config_under("true")

        self.assertFalse(live_search_allowed("2999-01-01", config=config))


def _truthy(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    unittest.main()
