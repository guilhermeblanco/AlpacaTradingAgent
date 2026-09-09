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
    _daemon_thread: Optional[threading.Thread] = None
    _db_initialized = False

    @staticmethod
    def _env_enabled(name: str, default: bool = False) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

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
                    broker TEXT NOT NULL DEFAULT 'alpaca',
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
                columns = {
                    row[1] for row in cursor.execute("PRAGMA table_info(virtual_stops)")
                }
                if "broker" not in columns:
                    cursor.execute(
                        "ALTER TABLE virtual_stops ADD COLUMN broker TEXT NOT NULL DEFAULT 'alpaca'"
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
        broker: str = "alpaca",
    ) -> Optional[int]:
        """Register a new active virtual stop for a symbol."""
        if not stop_loss_price and not take_profit_price:
            return None

        cls.init_db()
        norm_symbol = symbol.upper().strip()
        now = datetime.now(timezone.utc).isoformat()

        # Cancel any previous active virtual stops for this symbol to avoid duplicates
        cls.cancel_virtual_stops(norm_symbol, broker=broker)

        with cls._get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO virtual_stops (
                    broker, symbol, qty, notional, entry_price, stop_loss_price, take_profit_price, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
                """,
                (
                    broker,
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
    def get_active_virtual_stops(
        cls, symbol: Optional[str] = None, broker: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve active virtual stops."""
        cls.init_db()
        with cls._get_db_connection() as conn:
            cursor = conn.cursor()
            if symbol and broker:
                cursor.execute(
                    "SELECT * FROM virtual_stops WHERE status = 'ACTIVE' AND symbol = ? AND broker = ?",
                    (symbol.upper().strip(), broker),
                )
            elif symbol:
                norm_symbol = symbol.upper().strip()
                cursor.execute(
                    "SELECT * FROM virtual_stops WHERE status = 'ACTIVE' AND symbol = ?",
                    (norm_symbol,),
                )
            elif broker:
                cursor.execute(
                    "SELECT * FROM virtual_stops WHERE status = 'ACTIVE' AND broker = ?",
                    (broker,),
                )
            else:
                cursor.execute("SELECT * FROM virtual_stops WHERE status = 'ACTIVE'")
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    @classmethod
    def cancel_virtual_stops(cls, symbol: str, broker: Optional[str] = None) -> int:
        """Cancel all active virtual stops for a symbol."""
        cls.init_db()
        norm_symbol = symbol.upper().strip()
        now = datetime.now(timezone.utc).isoformat()
        with cls._get_db_connection() as conn:
            cursor = conn.cursor()
            if broker:
                cursor.execute(
                    "UPDATE virtual_stops SET status = 'CANCELLED', updated_at = ? WHERE symbol = ? AND broker = ? AND status = 'ACTIVE'",
                    (now, norm_symbol, broker),
                )
            else:
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
    def _set_stop_status(cls, stop_id: int, status: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with cls._get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE virtual_stops SET status = ?, updated_at = ? WHERE id = ? AND status = 'ACTIVE'",
                (status, now, stop_id),
            )
            conn.commit()
            return cursor.rowcount == 1

    @classmethod
    def reconcile_active_stops(
        cls, snapshot_provider=None, broker: Optional[str] = None
    ) -> Dict[str, int]:
        """Cancel stops that no longer correspond to an open Alpaca position.

        Broker errors propagate so startup fails closed instead of treating an
        unavailable account as empty and silently discarding protection.
        """
        active = cls.get_active_virtual_stops(broker=broker)
        if not active:
            return {"active": 0, "cancelled_orphans": 0}

        if snapshot_provider is None:
            from tradingagents.dataflows.alpaca_utils import get_alpaca_trading_client

            positions = get_alpaca_trading_client().get_all_positions()
            open_symbols = {
                str(position.symbol).upper().replace("/", "")
                for position in positions
                if float(position.qty) != 0
            }
        else:
            snapshot = snapshot_provider.get_portfolio_snapshot()
            open_symbols = {
                position.symbol.upper().replace("/", "")
                for position in snapshot.positions
                if position.quantity != 0
            }
        cancelled = 0
        for stop in active:
            symbol_key = str(stop["symbol"]).upper().replace("/", "")
            if symbol_key not in open_symbols and cls._set_stop_status(
                int(stop["id"]), "CANCELLED"
            ):
                cancelled += 1
                logger.warning(
                    "Cancelled orphaned Virtual Stop #%s for %s during startup reconciliation.",
                    stop["id"],
                    stop["symbol"],
                )
        return {"active": len(active) - cancelled, "cancelled_orphans": cancelled}

    @classmethod
    def check_and_trigger_stops(
        cls,
        symbol: str,
        current_price: float,
        *,
        snapshot_provider=None,
        close_position=None,
        broker: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Check active virtual stops against a new price tick and trigger market close
        if threshold is breached.
        """
        if current_price <= 0:
            return []

        active_stops = cls.get_active_virtual_stops(symbol, broker=broker)
        if not active_stops:
            return []

        legacy_alpaca = snapshot_provider is None and close_position is None
        if legacy_alpaca:
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
                # Confirm the position still exists immediately before any close.
                # A stale local stop must never create broker activity by itself.
                try:
                    if legacy_alpaca:
                        position_state = AlpacaUtils.get_current_position_state(
                            sym, strict=True
                        )
                    else:
                        position = snapshot_provider.get_portfolio_snapshot().position_for(sym)
                        position_state = position.side if position else "NEUTRAL"
                except Exception as exc:
                    logger.error(
                        "Cannot verify position for Virtual Stop #%s (%s): %s",
                        stop_id,
                        sym,
                        exc,
                    )
                    continue
                if position_state == "NEUTRAL":
                    cls._set_stop_status(stop_id, "CANCELLED")
                    logger.warning(
                        "Cancelled orphaned Virtual Stop #%s for %s before trigger.",
                        stop_id,
                        sym,
                    )
                    continue

                try:
                    from tradingagents.safety import get_safety_guard

                    verdict = get_safety_guard().check_order(
                        sym, 0.0, risk_reducing=True
                    )
                    if not verdict.allowed:
                        logger.error(
                            "Safety layer blocked Virtual Stop #%s for %s: %s",
                            stop_id,
                            sym,
                            " ".join(verdict.reasons),
                        )
                        continue
                except Exception as exc:
                    logger.error(
                        "Cannot verify safety state for Virtual Stop #%s (%s): %s",
                        stop_id,
                        sym,
                        exc,
                    )
                    continue

                logger.warning(
                    "TRIGGERING VIRTUAL STOP #%s for %s: %s", stop_id, sym, reason
                )
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
                    close_result = (
                        AlpacaUtils.close_position(sym)
                        if legacy_alpaca
                        else close_position(sym)
                    )
                    if not isinstance(close_result, dict) or not close_result.get("success"):
                        error = (
                            close_result.get("error", "broker rejected close")
                            if isinstance(close_result, dict)
                            else "broker returned an invalid close result"
                        )
                        raise RuntimeError(error)
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
    def start_realtime_daemon(cls) -> Dict[str, Any]:
        """Start monitoring only after explicit enablement and reconciliation."""
        if not cls._env_enabled("VIRTUAL_STOPS_DAEMON_ENABLED"):
            logger.info(
                "VirtualStops daemon is disabled; set VIRTUAL_STOPS_DAEMON_ENABLED=true to enable it."
            )
            return {"started": False, "reason": "disabled"}

        from tradingagents.broker import get_execution_broker_runtime

        runtime = get_execution_broker_runtime()
        is_paper = runtime.capabilities.paper_trading
        if not is_paper and not cls._env_enabled("VIRTUAL_STOPS_LIVE_ENABLED"):
            logger.error(
                "VirtualStops daemon refused to start for a live broker account. "
                "Set VIRTUAL_STOPS_LIVE_ENABLED=true only after operator review."
            )
            return {"started": False, "reason": "live_not_enabled"}

        with cls._instance_lock:
            if cls._daemon_running:
                return {"started": True, "reason": "already_running"}

        cls.init_db()
        try:
            reconciliation = cls.reconcile_active_stops(
                runtime.snapshot_provider, broker=runtime.name
            )
        except Exception as exc:
            logger.error("VirtualStops startup reconciliation failed: %s", exc)
            return {
                "started": False,
                "reason": "reconciliation_failed",
                "error": str(exc),
            }

        with cls._instance_lock:
            if cls._daemon_running:
                return {"started": True, "reason": "already_running"}
            cls._daemon_running = True

        def _daemon_loop():
            logger.info("Starting VirtualStops Real-Time Monitoring Daemon...")
            while cls._daemon_running:
                try:
                    active = cls.get_active_virtual_stops(broker=runtime.name)
                    if active:
                        symbols = list(set(s["symbol"] for s in active))
                        for sym in symbols:
                            try:
                                quote = runtime.snapshot_provider.get_quote_snapshot(sym)
                                price = quote.reference_price
                                if price and float(price) > 0:
                                    cls.check_and_trigger_stops(
                                        sym,
                                        float(price),
                                        snapshot_provider=runtime.snapshot_provider,
                                        close_position=runtime.execution_gateway.close_position,
                                        broker=runtime.name,
                                    )
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
        cls._daemon_thread = thread
        logger.info("VirtualStops Real-Time Monitoring Daemon initialized.")
        return {"started": True, "reason": "started", **reconciliation}

    @classmethod
    def stop_realtime_daemon(cls, timeout: float = 5.0) -> None:
        """Stop the process-local monitoring thread."""
        with cls._instance_lock:
            cls._daemon_running = False
            thread = cls._daemon_thread
            cls._daemon_thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
