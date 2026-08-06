# virtual_stops_manager.py

import os
import sqlite3
import time
import logging
import threading
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

logger = logging.getLogger("tradingagents.virtual_stops")

# Path for SQLite database storing virtual stops
DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "virtual_stops.db")


class VirtualStopsManager:
    """Manages software-based (virtual) Stop Loss and Take Profit levels for fractional
    and crypto orders that Alpaca API cannot protect natively via bracket orders.
    """

    _instance_lock = threading.Lock()
    _daemon_running = False
    _db_initialized = False

    @staticmethod
    def _get_db_connection() -> sqlite3.Connection:
        os.makedirs(DB_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    @classmethod
    def init_db(cls) -> None:
        """Initialize virtual stops database schema. Safe to call multiple times;
        actual work only happens once per process lifetime."""
        if cls._db_initialized:
            return
        with cls._instance_lock:
            if cls._db_initialized:
                return
            with cls._get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                CREATE TABLE IF NOT EXISTS virtual_stops (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    qty REAL,
                    notional REAL,
                    entry_price REAL,
                    stop_loss_price REAL,
                    take_profit_price REAL,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
                )
                conn.commit()
            cls._db_initialized = True
            cls.cleanup_old_stops(retention_days=30)

    @classmethod
    def cleanup_old_stops(cls, retention_days: int = 30) -> int:
        """Purge non-active virtual stops (TRIGGERED or CANCELLED) older than retention_days."""
        try:
            with cls._get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    DELETE FROM virtual_stops
                    WHERE status IN ('TRIGGERED', 'CANCELLED')
                    AND datetime(updated_at) < datetime('now', ? || ' days')
                    """,
                    (f"-{retention_days}",),
                )
                conn.commit()
                deleted = cursor.rowcount
                if deleted > 0:
                    logger.info(f"Cleaned up {deleted} old virtual stop records older than {retention_days} days.")
                return deleted
        except Exception as e:
            logger.error(f"Error during virtual stops database cleanup: {e}")
            return 0

    @classmethod
    def add_virtual_stop(
        cls,
        symbol: str,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        qty: Optional[float] = None,
        notional: Optional[float] = None,
        entry_price: Optional[float] = None,
    ) -> Optional[int]:
        """Register a new active virtual stop for a symbol."""
        if not stop_loss_price and not take_profit_price:
            return None

        cls.init_db()
        norm_symbol = symbol.upper().strip()
        now = datetime.now(timezone.utc).isoformat()

        # Cancel any previous active virtual stops for this symbol to avoid duplicates
        cls.cancel_virtual_stops(norm_symbol)

        with cls._get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO virtual_stops (
                    symbol, qty, notional, entry_price, stop_loss_price, take_profit_price, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
                """,
                (
                    norm_symbol,
                    qty,
                    notional,
                    entry_price,
                    stop_loss_price,
                    take_profit_price,
                    now,
                    now,
                ),
            )
            conn.commit()
            stop_id = cursor.lastrowid
            logger.info(
                f"Registered Virtual Stop #{stop_id} for {norm_symbol} "
                f"(SL={stop_loss_price}, TP={take_profit_price}, Notional={notional}, Qty={qty})"
            )
            return stop_id

    @classmethod
    def get_active_virtual_stops(cls, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve active virtual stops."""
        cls.init_db()
        with cls._get_db_connection() as conn:
            cursor = conn.cursor()
            if symbol:
                norm_symbol = symbol.upper().strip()
                cursor.execute(
                    "SELECT * FROM virtual_stops WHERE status = 'ACTIVE' AND symbol = ?",
                    (norm_symbol,),
                )
            else:
                cursor.execute("SELECT * FROM virtual_stops WHERE status = 'ACTIVE'")
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    @classmethod
    def cancel_virtual_stops(cls, symbol: str) -> int:
        """Cancel all active virtual stops for a symbol."""
        cls.init_db()
        norm_symbol = symbol.upper().strip()
        now = datetime.now(timezone.utc).isoformat()
        with cls._get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE virtual_stops SET status = 'CANCELLED', updated_at = ? WHERE symbol = ? AND status = 'ACTIVE'",
                (now, norm_symbol),
            )
            conn.commit()
            count = cursor.rowcount
            if count > 0:
                logger.info(f"Cancelled {count} virtual stops for {norm_symbol}")
            return count

    @classmethod
    def check_and_trigger_stops(cls, symbol: str, current_price: float) -> List[Dict[str, Any]]:
        """Check active virtual stops against a new price tick and trigger market close
        if threshold is breached.
        """
        if current_price <= 0:
            return []

        active_stops = cls.get_active_virtual_stops(symbol)
        if not active_stops:
            return []

        # Avoid circular import
        from tradingagents.dataflows.alpaca_utils import AlpacaUtils

        triggered = []
        for stop in active_stops:
            sl = stop.get("stop_loss_price")
            tp = stop.get("take_profit_price")
            sym = stop["symbol"]
            stop_id = stop["id"]

            reason = None
            if sl and current_price <= sl:
                reason = f"Virtual Stop Loss triggered: current price ${current_price:.2f} <= SL ${sl:.2f}"
            elif tp and current_price >= tp:
                reason = f"Virtual Take Profit triggered: current price ${current_price:.2f} >= TP ${tp:.2f}"

            if reason:
                logger.warning(f"🚨 TRIGGERING VIRTUAL STOP #{stop_id} for {sym}: {reason}")
                now = datetime.now(timezone.utc).isoformat()

                # Optimistic lock: only mark TRIGGERED if still ACTIVE (prevents double-trigger)
                with cls._get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE virtual_stops SET status = 'TRIGGERED', updated_at = ? WHERE id = ? AND status = 'ACTIVE'",
                        (now, stop_id),
                    )
                    conn.commit()
                    if cursor.rowcount == 0:
                        # Another thread already triggered this stop
                        logger.info(f"Virtual Stop #{stop_id} already triggered by another thread, skipping.")
                        continue

                # Execute position close via Alpaca
                try:
                    close_result = AlpacaUtils.close_position(sym)
                except Exception as e:
                    # If close fails, restore stop to ACTIVE so it can be retried
                    logger.error(f"Failed to close position for {sym} after stop trigger: {e}. Restoring stop to ACTIVE.")
                    with cls._get_db_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE virtual_stops SET status = 'ACTIVE', updated_at = ? WHERE id = ?",
                            (datetime.now(timezone.utc).isoformat(), stop_id),
                        )
                        conn.commit()
                    close_result = {"success": False, "error": str(e)}

                triggered.append(
                    {
                        "stop_id": stop_id,
                        "symbol": sym,
                        "reason": reason,
                        "trigger_price": current_price,
                        "result": close_result,
                    }
                )

        return triggered

    @classmethod
    def start_realtime_daemon(cls) -> None:
        """Start background thread monitoring price ticks via periodic polling."""
        with cls._instance_lock:
            if cls._daemon_running:
                return
            cls._daemon_running = True

        cls.init_db()

        def _daemon_loop():
            logger.info("Starting VirtualStops Real-Time Monitoring Daemon...")
            while cls._daemon_running:
                try:
                    active = cls.get_active_virtual_stops()
                    if active:
                        from tradingagents.dataflows.alpaca_utils import AlpacaUtils

                        symbols = list(set(s["symbol"] for s in active))
                        for sym in symbols:
                            try:
                                quote = AlpacaUtils.get_latest_quote(sym)
                                price = quote.get("ask_price") or quote.get("bid_price")
                                if price and float(price) > 0:
                                    cls.check_and_trigger_stops(sym, float(price))
                            except Exception as e:
                                logger.debug(f"Error checking quote for {sym}: {e}")
                    else:
                        # No active stops, sleep longer to avoid unnecessary API calls
                        time.sleep(8.0)
                        continue
                except Exception as e:
                    logger.error(f"Error in VirtualStops daemon: {e}")

                time.sleep(2.0)  # Check price ticks every 2 seconds for low latency

        thread = threading.Thread(target=_daemon_loop, daemon=True, name="VirtualStopsDaemon")
        thread.start()
        logger.info("VirtualStops Real-Time Monitoring Daemon initialized.")
