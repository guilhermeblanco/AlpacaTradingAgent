from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .models import LifecycleRecord, LifecycleStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LifecycleRepository:
    """SQLite projection of execution lifecycle state and transitions."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS lifecycle (
                    decision_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    valid_until TEXT,
                    run_id TEXT,
                    error TEXT,
                    result_json TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS lifecycle_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_lifecycle_status
                    ON lifecycle(status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_lifecycle_transitions_decision
                    ON lifecycle_transitions(decision_id, id);
                """
            )

    def get(self, decision_id: str) -> Optional[LifecycleRecord]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM lifecycle WHERE decision_id = ?", (decision_id,)
            ).fetchone()
        return self._record(row) if row else None

    def create(
        self,
        *,
        decision_id: str,
        symbol: str,
        idempotency_key: str,
        valid_until: Optional[datetime] = None,
        run_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> LifecycleRecord:
        now = _utcnow()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO lifecycle
                   (decision_id, symbol, status, idempotency_key, created_at,
                    updated_at, valid_until, run_id, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision_id,
                    symbol,
                    LifecycleStatus.RECEIVED.value,
                    idempotency_key,
                    now.isoformat(),
                    now.isoformat(),
                    valid_until.isoformat() if valid_until else None,
                    run_id,
                    json.dumps(metadata or {}, sort_keys=True),
                ),
            )
            if connection.total_changes:
                connection.execute(
                    """INSERT INTO lifecycle_transitions
                       (decision_id, recorded_at, from_status, to_status, payload_json)
                       VALUES (?, ?, NULL, ?, '{}')""",
                    (decision_id, now.isoformat(), LifecycleStatus.RECEIVED.value),
                )
        record = self.get(decision_id)
        if record is None:
            raise RuntimeError(f"Unable to create lifecycle record {decision_id}")
        return record

    def transition(
        self,
        decision_id: str,
        status: LifecycleStatus,
        *,
        error: Optional[str] = None,
        result: Optional[dict[str, Any]] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> LifecycleRecord:
        now = _utcnow()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM lifecycle WHERE decision_id = ?", (decision_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown lifecycle decision {decision_id}")
            previous = row["status"]
            connection.execute(
                """UPDATE lifecycle SET status = ?, updated_at = ?, error = ?,
                   result_json = COALESCE(?, result_json) WHERE decision_id = ?""",
                (
                    status.value,
                    now.isoformat(),
                    error,
                    json.dumps(result, sort_keys=True, default=str) if result is not None else None,
                    decision_id,
                ),
            )
            connection.execute(
                """INSERT INTO lifecycle_transitions
                   (decision_id, recorded_at, from_status, to_status, payload_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    decision_id,
                    now.isoformat(),
                    previous,
                    status.value,
                    json.dumps(payload or {}, sort_keys=True, default=str),
                ),
            )
        record = self.get(decision_id)
        if record is None:
            raise RuntimeError(f"Lifecycle record disappeared: {decision_id}")
        return record

    def expire_due(self, now: Optional[datetime] = None) -> int:
        now = now or _utcnow()
        candidates: list[str]
        terminal = tuple(status.value for status in (
            LifecycleStatus.SUCCEEDED,
            LifecycleStatus.BLOCKED,
            LifecycleStatus.FAILED,
            LifecycleStatus.EXPIRED,
            LifecycleStatus.CANCELLED,
        ))
        placeholders = ",".join("?" for _ in terminal)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"""SELECT decision_id FROM lifecycle
                    WHERE valid_until IS NOT NULL AND valid_until < ?
                    AND status NOT IN ({placeholders})""",
                (now.isoformat(), *terminal),
            ).fetchall()
            candidates = [row["decision_id"] for row in rows]
        for decision_id in candidates:
            self.transition(decision_id, LifecycleStatus.EXPIRED, error="Intent validity window elapsed")
        return len(candidates)

    @staticmethod
    def _record(row: sqlite3.Row) -> LifecycleRecord:
        return LifecycleRecord(
            decision_id=row["decision_id"],
            symbol=row["symbol"],
            status=LifecycleStatus(row["status"]),
            idempotency_key=row["idempotency_key"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            valid_until=datetime.fromisoformat(row["valid_until"]) if row["valid_until"] else None,
            run_id=row["run_id"],
            error=row["error"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            metadata=json.loads(row["metadata_json"] or "{}"),
        )
