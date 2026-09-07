from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _safe_symbol(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", symbol or "unknown")


def snapshot_hash(snapshot: Any) -> str:
    payload = snapshot.model_dump(mode="json") if hasattr(snapshot, "model_dump") else snapshot
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


class ExecutionJournal:
    """Append-only JSONL journal keyed by model decision id."""

    def __init__(self, results_dir: str | Path = "eval_results"):
        self.results_dir = Path(results_dir)
        self._lock = threading.RLock()

    def path_for(self, symbol: str, decision_id: str) -> Path:
        return (
            self.results_dir
            / _safe_symbol(symbol)
            / "TradingAgentsStrategy_logs"
            / "executions"
            / f"{decision_id}.jsonl"
        )

    def append(
        self,
        event_type: str,
        *,
        symbol: str,
        decision_id: str,
        run_id: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> str:
        path = self.path_for(symbol, decision_id)
        event = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "decision_id": decision_id,
            "run_id": run_id,
            "symbol": symbol,
            "payload": payload or {},
        }
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=True, default=str) + "\n")
        return str(path)
