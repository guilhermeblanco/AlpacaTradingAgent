from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


class AdmissionDecision(BaseModel):
    allowed: bool
    reason: str
    symbol: str
    admitted_at: Optional[datetime] = None


class AnalysisAdmissionPolicy:
    """Persistent, process-safe admission policy for symbol analysis."""

    def __init__(
        self,
        path: str | Path,
        *,
        cooldown_seconds: int = 86_400,
        material_price_move_pct: float = 3.0,
        daily_token_budget: int = 0,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cooldown_seconds = max(0, int(cooldown_seconds))
        self.material_price_move_pct = max(0.0, float(material_price_move_pct))
        self.daily_token_budget = max(0, int(daily_token_budget))
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS analysis_admission (
                    symbol TEXT PRIMARY KEY,
                    last_admitted_at TEXT,
                    last_price REAL,
                    in_flight INTEGER NOT NULL DEFAULT 0,
                    token_day TEXT,
                    tokens_used INTEGER NOT NULL DEFAULT 0
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level="IMMEDIATE")
        connection.row_factory = sqlite3.Row
        return connection

    def try_admit(
        self,
        symbol: str,
        *,
        price: Optional[float] = None,
        estimated_tokens: int = 0,
        now: Optional[datetime] = None,
    ) -> AdmissionDecision:
        symbol = symbol.upper().strip()
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("now must include a timezone")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM analysis_admission WHERE symbol = ?", (symbol,)
            ).fetchone()
            if row and row["in_flight"]:
                return AdmissionDecision(allowed=False, reason="analysis already in flight", symbol=symbol)
            day = now.astimezone(timezone.utc).date().isoformat()
            tokens_used = int(row["tokens_used"] or 0) if row and row["token_day"] == day else 0
            if self.daily_token_budget and tokens_used + max(0, estimated_tokens) > self.daily_token_budget:
                return AdmissionDecision(allowed=False, reason="daily token budget exceeded", symbol=symbol)
            if row and row["last_admitted_at"]:
                last = datetime.fromisoformat(row["last_admitted_at"])
                cooling_down = now < last + timedelta(seconds=self.cooldown_seconds)
                material_move = False
                if price and row["last_price"] and row["last_price"] > 0:
                    move = abs((price / row["last_price"] - 1.0) * 100.0)
                    material_move = move >= self.material_price_move_pct
                if cooling_down and not material_move:
                    return AdmissionDecision(allowed=False, reason="symbol cooldown active", symbol=symbol)
            connection.execute(
                """INSERT INTO analysis_admission
                   (symbol, last_admitted_at, last_price, in_flight, token_day, tokens_used)
                   VALUES (?, ?, ?, 1, ?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET
                     last_admitted_at=excluded.last_admitted_at,
                     last_price=COALESCE(excluded.last_price, analysis_admission.last_price),
                     in_flight=1, token_day=excluded.token_day, tokens_used=excluded.tokens_used""",
                (symbol, now.isoformat(), price, day, tokens_used + max(0, estimated_tokens)),
            )
        return AdmissionDecision(allowed=True, reason="admitted", symbol=symbol, admitted_at=now)

    def complete(self, symbol: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE analysis_admission SET in_flight = 0 WHERE symbol = ?",
                (symbol.upper().strip(),),
            )
