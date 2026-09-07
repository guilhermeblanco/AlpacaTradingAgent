from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class HeartbeatStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def beat(self, service: str, *, status: str = "healthy", details: Optional[dict[str, Any]] = None) -> dict:
        payload = {
            "service": service,
            "status": status,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "details": details or {},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)
        return payload

    def read(self) -> Optional[dict]:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text(encoding="utf-8"))

    def stale(self, max_age_seconds: float, *, now: Optional[datetime] = None) -> bool:
        payload = self.read()
        if payload is None:
            return True
        now = now or datetime.now(timezone.utc)
        recorded = datetime.fromisoformat(payload["recorded_at"])
        return (now - recorded).total_seconds() > max_age_seconds
