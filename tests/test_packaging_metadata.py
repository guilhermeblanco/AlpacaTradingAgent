from __future__ import annotations

import tomllib
from pathlib import Path


def test_pyproject_exposes_complete_and_component_install_profiles() -> None:
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]
    extras = project["optional-dependencies"]

    assert project["requires-python"] == ">=3.11"
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
