from __future__ import annotations

import importlib
import tomllib
from pathlib import Path


def test_pyproject_exposes_complete_and_component_install_profiles() -> None:
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]
    extras = project["optional-dependencies"]

    assert project["requires-python"] == ">=3.14"
    assert {"analysis", "brokers", "postgres", "cli", "web", "app", "dev"} <= set(
        extras
    )
    assert any(item.startswith("alpaca-py") for item in extras["brokers"])
    assert any(item.startswith("psycopg") for item in extras["postgres"])
    assert any(item.startswith("dash") for item in extras["web"])


def test_legacy_requirements_file_delegates_to_app_extra() -> None:
    requirements = [
        line.strip()
        for line in Path("requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]

    assert requirements == [".[app]"]


def _console_scripts() -> dict[str, str]:
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    return metadata["project"]["scripts"]


def test_every_long_running_worker_has_a_console_script() -> None:
    """Operators should not have to remember `python -m <dotted.path>`."""
    targets = set(_console_scripts().values())

    assert {
        "tradingagents.broker.preflight:main",
        "tradingagents.evaluation.worker:main",
        "tradingagents.execution.account_monitor:main",
        "tradingagents.execution.reconciliation_worker:main",
        "tradingagents.operations.control_plane:main",
        "tradingagents.orchestration.autonomous_worker:main",
    } <= targets


def test_every_console_script_target_resolves() -> None:
    for name, target in _console_scripts().items():
        module_path, _, attribute = target.partition(":")
        module = importlib.import_module(module_path)
        entry_point = getattr(module, attribute, None)
        assert entry_point is not None, f"{name} points at missing {target}"
        assert callable(entry_point), f"{name} target {target} is not callable"
