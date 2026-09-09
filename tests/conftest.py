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
