from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class SourceHealthLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def record_failure(
        self,
        *,
        provider: str,
        operation: str,
        error: str,
        symbol: Optional[str] = None,
        status_code: Optional[int] = None,
        retryable: bool = False,
        elapsed_ms: Optional[float] = None,
        correlation_id: Optional[str] = None,
    ) -> str:
        event = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "operation": operation,
            "symbol": symbol,
            "status_code": status_code,
            "retryable": retryable,
            "elapsed_ms": elapsed_ms,
            "correlation_id": correlation_id,
            "error": error,
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=True) + "\n")
        return str(self.path)
