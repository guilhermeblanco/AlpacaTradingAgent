"""Configuration-driven persistence runtime construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from .postgres import (
    DatabaseSettings,
    PostgresUnitOfWork,
    create_database_engine,
    create_session_factory,
)


@dataclass
class PersistenceRuntime:
    backend: str
    unit_of_work_factory: Optional[Callable[[], PostgresUnitOfWork]] = None
    _dispose: Optional[Callable[[], None]] = None

    def close(self) -> None:
        if self._dispose is not None:
            self._dispose()


def build_persistence_runtime(config: dict[str, Any]) -> PersistenceRuntime:
    backend = str(config.get("persistence_backend") or "local").strip().lower()
    if backend == "local":
        return PersistenceRuntime(backend="local")
    if backend != "postgres":
        raise ValueError(f"Unsupported persistence backend: {backend}")

    database_url = str(config.get("database_url") or "").strip()
    settings = (
        DatabaseSettings(url=database_url)
        if database_url
        else DatabaseSettings.from_env()
    )
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    admission_options = {
        "material_price_move_pct": config.get(
            "analysis_material_price_move_pct", 3.0
        ),
        "daily_token_budget": config.get("daily_llm_token_budget", 0),
    }
    return PersistenceRuntime(
        backend="postgres",
        unit_of_work_factory=lambda: PostgresUnitOfWork(
            session_factory, admission_options=admission_options
        ),
        _dispose=engine.dispose,
    )
