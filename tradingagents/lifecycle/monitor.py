from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .repository import LifecycleRepository


class LifecycleMonitor:
    """Bounded lifecycle maintenance loop with a persisted heartbeat."""

    def __init__(
        self,
        repository: LifecycleRepository,
        *,
        interval_seconds: float = 30,
        heartbeat_path: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.repository = repository
        self.interval_seconds = max(0.1, float(interval_seconds))
        self.heartbeat_path = Path(heartbeat_path) if heartbeat_path else None
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._stop = threading.Event()

    def run_once(self) -> int:
        now = self.clock()
        expired = self.repository.expire_due(now)
        if self.heartbeat_path:
            self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            self.heartbeat_path.write_text(now.isoformat(), encoding="utf-8")
        return expired

    def run(self, *, max_iterations: Optional[int] = None) -> None:
        iterations = 0
        while not self._stop.is_set():
            self.run_once()
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                return
            self._stop.wait(self.interval_seconds)

    def stop(self) -> None:
        self._stop.set()
