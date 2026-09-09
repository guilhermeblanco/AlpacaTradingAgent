"""The WebUI shares one persistence runtime across callbacks.

The cockpit refreshes every 15s and the decision explorer every 30s, per
open tab. Building a runtime per tick creates and disposes a SQLAlchemy
engine each time, so pooling never applies.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from webui.utils import persistence


class FakeRuntime:
    def __init__(self, backend):
        self.backend = backend
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _clean_cache():
    persistence.reset_persistence_runtime()
    yield
    persistence.reset_persistence_runtime()


def _patched(config, builds):
    def build(cfg):
        builds.append(cfg)
        return FakeRuntime(cfg.get("persistence_backend"))

    return (
        mock.patch("tradingagents.dataflows.config.get_config", lambda: config),
        mock.patch("tradingagents.persistence.build_persistence_runtime", build),
    )


def test_repeated_calls_reuse_one_runtime() -> None:
    config = {"persistence_backend": "postgres", "database_url": "postgresql://x/y"}
    builds = []
    get_config, build = _patched(config, builds)
    with get_config, build:
        first = persistence.get_persistence_runtime()
        second = persistence.get_persistence_runtime()

    assert first is second
    assert len(builds) == 1


def test_configuration_change_rebuilds_and_disposes_the_old_runtime() -> None:
    config = {"persistence_backend": "postgres", "database_url": "postgresql://x/y"}
    builds = []
    get_config, build = _patched(config, builds)
    with get_config, build:
        first = persistence.get_persistence_runtime()
        config["database_url"] = "postgresql://x/other"
        second = persistence.get_persistence_runtime()

    assert first is not second
    assert first.closed
    assert not second.closed
    assert len(builds) == 2


def test_reset_disposes_the_cached_runtime() -> None:
    config = {"persistence_backend": "postgres"}
    builds = []
    get_config, build = _patched(config, builds)
    with get_config, build:
        runtime = persistence.get_persistence_runtime()
    persistence.reset_persistence_runtime()

    assert runtime.closed


def test_callbacks_do_not_close_the_shared_runtime() -> None:
    """A callback closing it would dispose the engine other tabs are using."""
    from pathlib import Path

    for name in ("operations_callbacks.py", "decision_explorer_callbacks.py"):
        source = Path("webui/callbacks", name).read_text(encoding="utf-8")
        assert "runtime.close()" not in source, f"{name} disposes the shared runtime"
