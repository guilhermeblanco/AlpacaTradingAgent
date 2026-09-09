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
