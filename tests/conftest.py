"""Shared fixtures for the test suite.

The PostgreSQL-backed tests run against a real database selected with
``TEST_DATABASE_URL``. That database is reused across runs, so every test
starts from a truncated schema: without this, rows written by a previous
run leak into assertions that expect to see only their own data, and the
suite is green only on a freshly created database.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def postgres_database_url() -> str:
    """URL of the throwaway PostgreSQL database, or skip the test."""
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return database_url


@pytest.fixture(scope="session")
def migrated_postgres_engine(postgres_database_url: str):
    """Engine bound to a database migrated to head exactly once per run."""
    from alembic import command
    from alembic.config import Config

    from tradingagents.persistence.postgres import (
        DatabaseSettings,
        create_database_engine,
    )

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", postgres_database_url)
    command.upgrade(config, "head")

    engine = create_database_engine(DatabaseSettings(url=postgres_database_url))
    try:
        yield engine
    finally:
        engine.dispose()


def _truncate_persistence_tables(engine) -> None:
    from sqlalchemy import text

    from tradingagents.persistence.postgres import Base

    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    if not tables:
        return
    with engine.begin() as connection:
        connection.execute(
            text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE")
        )


@pytest.fixture
def postgres_engine(migrated_postgres_engine):
    """Migrated engine with every persistence table emptied first."""
    _truncate_persistence_tables(migrated_postgres_engine)
    return migrated_postgres_engine


@pytest.fixture
def postgres_session_factory(postgres_engine):
    from tradingagents.persistence.postgres import create_session_factory

    return create_session_factory(postgres_engine)


def dash_callback(app, output_key: str):
    """The undecorated function registered for a Dash output.

    Dash wraps every callback in a dispatcher that expects the server's
    request context, so tests reach past it to the function itself.
    Multi-output callbacks are keyed as "..a.children...b.children..", so
    the key is split into exact outputs rather than matched as a substring.
    """

    def outputs(key: str) -> list[str]:
        if key.startswith("..") and key.endswith(".."):
            return key[2:-2].split("...")
        return [key]

    matched = []
    for key, spec in app.callback_map.items():
        for item in outputs(key):
            # Pattern-matching and allow_duplicate outputs carry a hash
            # suffix after "@".
            name, _, suffix = item.partition("@")
            if name == output_key:
                matched.append((bool(suffix), spec["callback"]))
                break

    if not matched:
        raise KeyError(f"no callback registered for {output_key!r}")
    # An allow_duplicate registration shadows the primary one; prefer the
    # primary when both write the same output.
    primary = [callback for duplicate, callback in matched if not duplicate]
    candidates = primary or [callback for _duplicate, callback in matched]
    if len(candidates) > 1:
        raise KeyError(f"{output_key!r} matches {len(candidates)} callbacks")
    return candidates[0].__wrapped__


_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", ""}


@pytest.fixture(autouse=True)
def _no_outbound_network(monkeypatch):
    """Fail any test that reaches a remote host.

    The suite is meant to run offline with every external boundary mocked.
    A missed patch otherwise turns into a real API call — slow, chargeable,
    and green or red depending on someone's credentials. Loopback stays open
    for the PostgreSQL tests.
    """
    import socket

    real_connect = socket.socket.connect
    real_create_connection = socket.create_connection

    def _is_local(address) -> bool:
        if not isinstance(address, tuple) or not address:
            return True
        host = address[0]
        return host in _LOCAL_HOSTS or str(host).startswith("127.")

    def guarded_connect(self, address, *args, **kwargs):
        if not _is_local(address):
            raise AssertionError(
                f"test attempted a network connection to {address!r}; "
                "mock the boundary instead"
            )
        return real_connect(self, address, *args, **kwargs)

    def guarded_create_connection(address, *args, **kwargs):
        if not _is_local(address):
            raise AssertionError(
                f"test attempted a network connection to {address!r}; "
                "mock the boundary instead"
            )
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
