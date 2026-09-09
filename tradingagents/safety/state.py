"""Mutable safety-state storage contracts and PostgreSQL implementation."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Optional, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from tradingagents.persistence.postgres.models import SafetyStateRow, SafetyTokenUsageRow


class SafetyStateStore(Protocol):
    def kill_switch(self) -> tuple[bool, str]: ...

    def set_kill_switch(self, active: bool, reason: str = "") -> None: ...

    def record_order_result(self, success: bool) -> None: ...

    def consecutive_rejections(self) -> int: ...

    def record_llm_tokens(self, usage_day: date, tokens: int) -> None: ...

    def llm_tokens_used(self, usage_day: date) -> int: ...

    def update_high_water_mark(self, equity: float) -> float: ...

    def close(self) -> None: ...


class PostgresSafetyStateStore:
    """Transaction-per-operation safety store shared by every process."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        scope: str = "default",
        dispose=None,
    ):
        self.session_factory = session_factory
        self.scope = scope.strip() or "default"
        self._dispose = dispose

    def _ensure_state(self, session: Session) -> SafetyStateRow:
        values = {
            "scope": self.scope,
            "kill_switch_active": False,
            "consecutive_rejections": 0,
            "updated_at": datetime.now(timezone.utc),
        }
        dialect = session.bind.dialect.name
        if dialect == "postgresql":
            session.execute(
                postgresql_insert(SafetyStateRow)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["scope"])
            )
        elif dialect == "sqlite":
            session.execute(
                sqlite_insert(SafetyStateRow)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["scope"])
            )
        else:
            if session.get(SafetyStateRow, self.scope) is None:
                session.add(SafetyStateRow(**values))
                session.flush()
        row = session.scalar(
            select(SafetyStateRow)
            .where(SafetyStateRow.scope == self.scope)
            .with_for_update()
        )
        if row is None:
            raise RuntimeError(f"Unable to initialize safety state for {self.scope}")
        return row

    def kill_switch(self) -> tuple[bool, str]:
        with self.session_factory() as session:
            row = self._ensure_state(session)
            session.commit()
            return bool(row.kill_switch_active), row.kill_switch_reason or ""

    def set_kill_switch(self, active: bool, reason: str = "") -> None:
        with self.session_factory() as session:
            row = self._ensure_state(session)
            row.kill_switch_active = bool(active)
            row.kill_switch_reason = reason if active else None
            row.kill_switch_changed_at = datetime.now(timezone.utc)
            row.updated_at = row.kill_switch_changed_at
            session.commit()

    def record_order_result(self, success: bool) -> None:
        with self.session_factory() as session:
            row = self._ensure_state(session)
            row.consecutive_rejections = (
                0 if success else int(row.consecutive_rejections) + 1
            )
            row.updated_at = datetime.now(timezone.utc)
            session.commit()

    def consecutive_rejections(self) -> int:
        with self.session_factory() as session:
            row = self._ensure_state(session)
            session.commit()
            return int(row.consecutive_rejections)

    def record_llm_tokens(self, usage_day: date, tokens: int) -> None:
        values = {"scope": self.scope, "usage_day": usage_day, "tokens": int(tokens)}
        with self.session_factory() as session:
            dialect = session.bind.dialect.name
            if dialect == "postgresql":
                statement = postgresql_insert(SafetyTokenUsageRow).values(**values)
                statement = statement.on_conflict_do_update(
                    index_elements=["scope", "usage_day"],
                    set_={"tokens": SafetyTokenUsageRow.tokens + int(tokens)},
                )
                session.execute(statement)
            elif dialect == "sqlite":
                statement = sqlite_insert(SafetyTokenUsageRow).values(**values)
                statement = statement.on_conflict_do_update(
                    index_elements=["scope", "usage_day"],
                    set_={"tokens": SafetyTokenUsageRow.tokens + int(tokens)},
                )
                session.execute(statement)
            else:
                row = session.get(SafetyTokenUsageRow, (self.scope, usage_day))
                if row is None:
                    session.add(SafetyTokenUsageRow(**values))
                else:
                    row.tokens += int(tokens)
            session.commit()

    def llm_tokens_used(self, usage_day: date) -> int:
        with self.session_factory() as session:
            row = session.get(SafetyTokenUsageRow, (self.scope, usage_day))
            return int(row.tokens) if row is not None else 0

    def update_high_water_mark(self, equity: float) -> float:
        with self.session_factory() as session:
            row = self._ensure_state(session)
            current = row.high_water_mark
            if current is None or equity > float(current):
                row.high_water_mark = equity
                row.updated_at = datetime.now(timezone.utc)
            session.commit()
            return float(row.high_water_mark)

    def close(self) -> None:
        if self._dispose is not None:
            self._dispose()
            self._dispose = None


def build_postgres_safety_store(config: dict) -> PostgresSafetyStateStore:
    from tradingagents.persistence.postgres import (
        DatabaseSettings,
        create_database_engine,
        create_session_factory,
    )

    database_url = str(config.get("database_url") or "").strip()
    settings = DatabaseSettings(url=database_url) if database_url else DatabaseSettings.from_env()
    engine = create_database_engine(settings)
    scope = str(
        config.get("safety_state_scope")
        or config.get("autonomous_account_key")
        or os.getenv("SAFETY_STATE_SCOPE")
        or os.getenv("AUTONOMOUS_ACCOUNT_KEY")
        or "default"
    )
    return PostgresSafetyStateStore(
        create_session_factory(engine), scope=scope, dispose=engine.dispose
    )
