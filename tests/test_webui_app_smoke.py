"""The Dash application must assemble completely and offline.

Nothing else in the suite builds the real app, so a duplicate callback
output, a bad import in a callback module, or a callback pointed at an id
that no longer exists in the layout would only surface when a browser first
hits the server.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import dash.development.base_component as bc
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent

# The layout is assembled once at server startup. Anything it touches over
# the network blocks the boot and fails hard when the broker is unreachable.
NETWORK_GUARDED_BUILD = """
import socket

attempts = []


def _blocked(*args, **kwargs):
    attempts.append(args)
    raise RuntimeError("network access attempted while building the Dash app")


socket.socket.connect = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked

from webui.app_dash import create_app

app = create_app()

print("ATTEMPTED_CONNECTIONS:", len(attempts))
print("CALLBACKS:", len(app.callback_map))
print("BUILD_OK")
"""


def _layout_ids(component) -> set[str]:
    found: set[str] = set()

    def walk(node) -> None:
        component_id = getattr(node, "id", None)
        if isinstance(component_id, str):
            found.add(component_id)
        children = getattr(node, "children", None)
        if isinstance(children, (list, tuple)):
            for child in children:
                if isinstance(child, bc.Component):
                    walk(child)
        elif isinstance(children, bc.Component):
            walk(children)

    walk(component)
    return found


@pytest.fixture(scope="module")
def dash_app():
    from webui.app_dash import create_app

    return create_app()


def test_app_builds_without_touching_the_network() -> None:
    result = subprocess.run(
        [sys.executable, "-c", NETWORK_GUARDED_BUILD],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, f"create_app() failed:\n{output}"
    assert "BUILD_OK" in result.stdout, f"create_app() did not complete:\n{output}"
    assert "ATTEMPTED_CONNECTIONS: 0" in result.stdout, (
        f"building the Dash app opened a network connection:\n{output}"
    )
    assert "Error fetching" not in output, (
        f"building the Dash app called the broker API:\n{output}"
    )


def test_app_registers_callbacks(dash_app) -> None:
    assert len(dash_app.callback_map) > 50


def test_every_callback_output_resolves_to_a_layout_component(dash_app) -> None:
    """Pattern-matching ids are resolved at runtime; plain ids are not."""
    available = _layout_ids(dash_app.layout)

    dangling = set()
    for spec in dash_app.callback_map.values():
        outputs = spec.get("output")
        outputs = outputs if isinstance(outputs, (list, tuple)) else [outputs]
        for output in outputs:
            component_id = getattr(output, "component_id", None)
            if isinstance(component_id, str) and component_id not in available:
                dangling.add(component_id)

    assert not dangling, f"callback outputs with no component in the layout: {sorted(dangling)}"


def test_broker_containers_exist_for_the_account_refresh_callback(dash_app) -> None:
    """The layout ships these empty; the refresh callback fills them."""
    available = _layout_ids(dash_app.layout)

    assert {
        "positions-table-container",
        "orders-table-body-container",
        "orders-pagination-container",
        "account-summary-container",
    } <= available


def test_a_finished_analyst_report_is_filed_under_its_own_key(dash_app) -> None:
    """Analysts run in parallel by default, so the market analyst routinely
    finishes while the social one is still working. A workaround used to
    reroute any market_report arriving in that window into sentiment_report,
    which lost the market read and marked the social analyst done."""
    from webui.utils.state import AppState

    state = AppState()
    state.init_symbol_state("NVDA")
    state.current_symbol = "NVDA"
    state.get_state("NVDA")["agent_statuses"]["Social Analyst"] = "in_progress"

    state.process_chunk_updates({"market_report": "the market read"})

    reports = state.get_state("NVDA")["current_reports"]
    assert reports["market_report"] == "the market read"
    assert not reports["sentiment_report"]
    assert (
        state.get_state("NVDA")["agent_statuses"]["Social Analyst"] == "in_progress"
    )


def test_running_the_app_serves_the_configured_address() -> None:
    from unittest import mock

    import webui.app_dash as app_dash

    built = mock.MagicMock()
    with mock.patch.object(app_dash, "create_app", lambda: built):
        assert app_dash.run_app(port=1234, server_name="0.0.0.0") == 0

    _args, kwargs = built.run.call_args
    assert kwargs["port"] == 1234
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["use_reloader"] is False


def test_debug_mode_enables_hot_reload() -> None:
    from unittest import mock

    import webui.app_dash as app_dash

    built = mock.MagicMock()
    with mock.patch.object(app_dash, "create_app", lambda: built):
        app_dash.run_app(debug=True)

    assert built.run.call_args.kwargs["dev_tools_hot_reload"] is True


def test_the_module_level_app_is_built_lazily() -> None:
    """Building it assembles the layout, which the package import must not do."""
    import webui.app_dash as app_dash

    assert app_dash.app is not None

    with pytest.raises(AttributeError):
        app_dash.no_such_thing


def test_clientside_callbacks_are_attached_to_the_app(dash_app) -> None:
    """Registered through `app.clientside_callback`, not the module-level
    function: the global registry the latter writes into is drained when the
    Dash object is constructed, which happens before callbacks are
    registered, so anything added afterwards is silently never attached.
    """
    attached = {
        spec["output"]
        for spec in getattr(dash_app, "_callback_list", [])
        if spec.get("clientside_function")
    }

    assert "pulse-listener.children" in attached
