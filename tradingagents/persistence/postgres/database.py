"""Database configuration and SQLAlchemy factories."""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


@dataclass(frozen=True)
class DatabaseSettings:
    url: str
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout_seconds: int = 30
    statement_timeout_ms: int = 30_000

    @classmethod
    def from_env(cls) -> "DatabaseSettings":
        url = os.getenv("DATABASE_URL", "").strip()
        if not url:
            raise ValueError("DATABASE_URL is required for PostgreSQL persistence")
        return cls(
            url=url,
            pool_size=int(os.getenv("DATABASE_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("DATABASE_MAX_OVERFLOW", "10")),
            pool_timeout_seconds=int(os.getenv("DATABASE_POOL_TIMEOUT_SECONDS", "30")),
            statement_timeout_ms=int(
                os.getenv("DATABASE_STATEMENT_TIMEOUT_MS", "30000")
            ),
        )


def create_database_engine(settings: DatabaseSettings) -> Engine:
    options = {"pool_pre_ping": True}
    if settings.url.startswith("postgresql"):
        options.update(
            pool_size=max(1, settings.pool_size),
            max_overflow=max(0, settings.max_overflow),
            pool_timeout=max(1, settings.pool_timeout_seconds),
            connect_args={
                "options": f"-c statement_timeout={max(1, settings.statement_timeout_ms)}"
            },
        )
    return create_engine(settings.url, **options)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
