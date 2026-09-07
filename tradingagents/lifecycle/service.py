from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .models import LifecycleRecord, LifecycleStatus
from .repository import LifecycleRepository


class DuplicateExecution(RuntimeError):
    def __init__(self, record: LifecycleRecord):
        super().__init__(f"Decision {record.decision_id} is already {record.status.value}")
        self.record = record


class LifecycleService:
    def __init__(self, repository: LifecycleRepository, *, default_ttl_seconds: int = 900):
        self.repository = repository
        self.default_ttl_seconds = max(1, int(default_ttl_seconds))

    @staticmethod
    def idempotency_key(decision_id: str, symbol: str) -> str:
        digest = hashlib.sha256(f"{decision_id}:{symbol.upper()}".encode()).hexdigest()[:24]
        return f"ata-{digest}"

    def begin(
        self,
        *,
        decision_id: str,
        symbol: str,
        run_id: Optional[str] = None,
        valid_until: Optional[datetime] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> LifecycleRecord:
        existing = self.repository.get(decision_id)
        if existing is not None:
            raise DuplicateExecution(existing)
        if valid_until is None:
            valid_until = datetime.now(timezone.utc) + timedelta(seconds=self.default_ttl_seconds)
        return self.repository.create(
            decision_id=decision_id,
            symbol=symbol,
            idempotency_key=self.idempotency_key(decision_id, symbol),
            valid_until=valid_until,
            run_id=run_id,
            metadata=metadata,
        )

    def transition(self, decision_id: str, status: LifecycleStatus, **kwargs) -> LifecycleRecord:
        return self.repository.transition(decision_id, status, **kwargs)
