# test_virtual_stops_and_fractional_buys.py

import os
import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import tradingagents.dataflows.virtual_stops_manager as virtual_stops_module
from tradingagents.dataflows.virtual_stops_manager import VirtualStopsManager
from tradingagents.dataflows.alpaca_utils import AlpacaUtils


@pytest.fixture(autouse=True)
def isolated_virtual_stops_database(tmp_path, monkeypatch):
    """Keep tests and developer runs from sharing executable stop records."""
    VirtualStopsManager.stop_realtime_daemon(timeout=0)
    monkeypatch.setattr(virtual_stops_module, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(
        virtual_stops_module, "DB_PATH", str(tmp_path / "virtual_stops.db")
    )
    VirtualStopsManager._db_initialized = False
    yield
    VirtualStopsManager.stop_realtime_daemon(timeout=0)
    VirtualStopsManager._db_initialized = False


def _allow_virtual_stop():
    guard = MagicMock()
    guard.check_order.return_value = SimpleNamespace(allowed=True, reasons=[])
    return guard


def test_virtual_stops_crud(tmp_path):
    """Test registering, retrieving, and cancelling virtual stops."""
    symbol = "NVDA"
    stop_id = VirtualStopsManager.add_virtual_stop(
        symbol=symbol,
        stop_loss_price=110.0,
        take_profit_price=140.0,
        notional=50.0,
        entry_price=120.0,
    )
    assert stop_id is not None

    active = VirtualStopsManager.get_active_virtual_stops(symbol)
    assert len(active) == 1
    assert active[0]["symbol"] == symbol
    assert active[0]["stop_loss_price"] == 110.0
    assert active[0]["take_profit_price"] == 140.0
    assert active[0]["notional"] == 50.0

    # Cancel stop
    cancelled_count = VirtualStopsManager.cancel_virtual_stops(symbol)
    assert cancelled_count == 1

    active_after = VirtualStopsManager.get_active_virtual_stops(symbol)
    assert len(active_after) == 0


def test_virtual_stop_trigger_sl():
    """Test triggering Virtual Stop Loss when current price <= SL threshold."""
    symbol = "TSLA"
    VirtualStopsManager.add_virtual_stop(
        symbol=symbol,
        stop_loss_price=200.0,
        take_profit_price=250.0,
        notional=100.0,
    )

    with patch.object(AlpacaUtils, "close_position", return_value={"success": True, "order_id": "mock_close_1"}) as mock_close, \
         patch.object(AlpacaUtils, "get_current_position_state", return_value="LONG"), \
         patch("tradingagents.safety.get_safety_guard", return_value=_allow_virtual_stop()):
        # Price above SL: no trigger
        triggered = VirtualStopsManager.check_and_trigger_stops(symbol, current_price=210.0)
        assert len(triggered) == 0
        mock_close.assert_not_called()

        # Price drops to or below SL: trigger stop loss
        triggered = VirtualStopsManager.check_and_trigger_stops(symbol, current_price=198.0)
        assert len(triggered) == 1
        assert triggered[0]["symbol"] == symbol
        assert "Stop Loss" in triggered[0]["reason"]
        mock_close.assert_called_once_with(symbol)


def test_virtual_stop_trigger_tp():
    """Test triggering Virtual Take Profit when current price >= TP threshold."""
    symbol = "AAPL"
    VirtualStopsManager.add_virtual_stop(
        symbol=symbol,
        stop_loss_price=180.0,
        take_profit_price=220.0,
        notional=75.0,
    )

    with patch.object(AlpacaUtils, "close_position", return_value={"success": True, "order_id": "mock_close_2"}) as mock_close, \
         patch.object(AlpacaUtils, "get_current_position_state", return_value="LONG"), \
         patch("tradingagents.safety.get_safety_guard", return_value=_allow_virtual_stop()):
        # Price rises above TP: trigger take profit
        triggered = VirtualStopsManager.check_and_trigger_stops(symbol, current_price=225.0)
        assert len(triggered) == 1
        assert triggered[0]["symbol"] == symbol
        assert "Take Profit" in triggered[0]["reason"]
        mock_close.assert_called_once_with(symbol)


def test_fractional_buy_when_budget_low():
    """Test that AlpacaUtils.execute_trading_action executes a fractional buy
    with notional amount when budget is lower than 1 share price.
    """
    symbol = "MSFT"
    budget = 50.0  # Budget $50, while MSFT price is $400 (qty < 1)

    disabled_guard = MagicMock()
    disabled_guard.enabled = False

    with patch("tradingagents.safety.get_safety_guard", return_value=disabled_guard), \
         patch.object(AlpacaUtils, "get_latest_quote", return_value={"bid_price": 400.0, "ask_price": 400.0}), \
         patch.object(AlpacaUtils, "place_market_order") as mock_place_order, \
         patch.object(VirtualStopsManager, "add_virtual_stop") as mock_add_stop:

        mock_place_order.return_value = {
            "success": True,
            "order_id": "mock_frac_order_123",
            "symbol": symbol,
            "notional": budget,
            "status": "accepted",
        }

        # Execute BUY with $50 budget on $400 MSFT (qty_int would be 0)
        res = AlpacaUtils.execute_trading_action(
            symbol=symbol,
            current_position="NEUTRAL",
            signal="BUY",
            dollar_amount=budget,
            protective_prices={"stop_loss_price": 370.0, "take_profit_price": 450.0},
        )

        assert res.get("success") is True
        # Verify place_market_order was called with notional=50.0
        mock_place_order.assert_called_once_with(symbol, "buy", notional=budget)
        # Verify virtual stop was registered for the fractional order
        mock_add_stop.assert_called_once_with(
            symbol=symbol,
            stop_loss_price=370.0,
            take_profit_price=450.0,
            notional=budget,
        )


def test_daemon_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VIRTUAL_STOPS_DAEMON_ENABLED", raising=False)
    with patch.object(VirtualStopsManager, "reconcile_active_stops") as reconcile:
        status = VirtualStopsManager.start_realtime_daemon()
    assert status == {"started": False, "reason": "disabled"}
    reconcile.assert_not_called()


def test_live_daemon_requires_separate_enablement(monkeypatch):
    monkeypatch.setenv("VIRTUAL_STOPS_DAEMON_ENABLED", "true")
    monkeypatch.setenv("ALPACA_USE_PAPER", "false")
    monkeypatch.delenv("VIRTUAL_STOPS_LIVE_ENABLED", raising=False)
    with patch.object(VirtualStopsManager, "reconcile_active_stops") as reconcile:
        status = VirtualStopsManager.start_realtime_daemon()
    assert status == {"started": False, "reason": "live_not_enabled"}
    reconcile.assert_not_called()


def test_startup_reconciliation_cancels_orphaned_stops():
    VirtualStopsManager.add_virtual_stop("BTC/USD", take_profit_price=70_000)
    client = MagicMock()
    client.get_all_positions.return_value = []
    with patch(
        "tradingagents.dataflows.alpaca_utils.get_alpaca_trading_client",
        return_value=client,
    ):
        result = VirtualStopsManager.reconcile_active_stops()
    assert result == {"active": 0, "cancelled_orphans": 1}
    assert VirtualStopsManager.get_active_virtual_stops() == []


def test_startup_reconciliation_preserves_stops_with_positions():
    VirtualStopsManager.add_virtual_stop("BTC/USD", take_profit_price=70_000)
    client = MagicMock()
    client.get_all_positions.return_value = [
        SimpleNamespace(symbol="BTCUSD", qty="0.01")
    ]
    with patch(
        "tradingagents.dataflows.alpaca_utils.get_alpaca_trading_client",
        return_value=client,
    ):
        result = VirtualStopsManager.reconcile_active_stops()
    assert result == {"active": 1, "cancelled_orphans": 0}
    assert len(VirtualStopsManager.get_active_virtual_stops()) == 1


def test_startup_reconciliation_failure_prevents_daemon(monkeypatch):
    monkeypatch.setenv("VIRTUAL_STOPS_DAEMON_ENABLED", "true")
    monkeypatch.setenv("ALPACA_USE_PAPER", "true")
    with patch.object(
        VirtualStopsManager,
        "reconcile_active_stops",
        side_effect=RuntimeError("broker unavailable"),
    ):
        status = VirtualStopsManager.start_realtime_daemon()
    assert not status["started"]
    assert status["reason"] == "reconciliation_failed"
    assert not VirtualStopsManager._daemon_running


def test_failed_close_restores_stop_for_retry():
    symbol = "MSFT"
    VirtualStopsManager.add_virtual_stop(symbol, stop_loss_price=300)
    with patch.object(
        AlpacaUtils,
        "close_position",
        return_value={"success": False, "error": "broker rejected close"},
    ) as close, patch.object(
        AlpacaUtils, "get_current_position_state", return_value="LONG"
    ), patch(
        "tradingagents.safety.get_safety_guard", return_value=_allow_virtual_stop()
    ):
        triggered = VirtualStopsManager.check_and_trigger_stops(symbol, 290)
    assert len(triggered) == 1
    assert not triggered[0]["result"]["success"]
    assert len(VirtualStopsManager.get_active_virtual_stops(symbol)) == 1
    close.assert_called_once_with(symbol)


def test_trigger_cancels_stop_when_position_no_longer_exists():
    symbol = "AAPL"
    VirtualStopsManager.add_virtual_stop(symbol, take_profit_price=200)
    with patch.object(
        AlpacaUtils, "get_current_position_state", return_value="NEUTRAL"
    ), patch.object(AlpacaUtils, "close_position") as close:
        assert VirtualStopsManager.check_and_trigger_stops(symbol, 210) == []
    assert VirtualStopsManager.get_active_virtual_stops(symbol) == []
    close.assert_not_called()


def test_kill_switch_blocks_trigger_and_keeps_stop_active():
    symbol = "AAPL"
    VirtualStopsManager.add_virtual_stop(symbol, stop_loss_price=180)
    guard = MagicMock()
    guard.check_order.return_value = SimpleNamespace(
        allowed=False, reasons=["Kill switch is engaged"]
    )
    with patch.object(
        AlpacaUtils, "get_current_position_state", return_value="LONG"
    ), patch.object(AlpacaUtils, "close_position") as close, patch(
        "tradingagents.safety.get_safety_guard", return_value=guard
    ):
        assert VirtualStopsManager.check_and_trigger_stops(symbol, 170) == []
    assert len(VirtualStopsManager.get_active_virtual_stops(symbol)) == 1
    close.assert_not_called()


def test_close_position_cancels_existing_open_orders():
    """Test that close_position cancels open orders for the symbol prior to closing."""
    mock_client = MagicMock()
    mock_order_1 = MagicMock(id="order_swim_123")
    mock_client.get_orders.return_value = [mock_order_1]
    
    mock_close_order = MagicMock(id="close_order_999", symbol="SWIM", side="sell", qty=8, status="accepted")
    mock_client.close_position.return_value = mock_close_order

    with patch("tradingagents.dataflows.alpaca_utils.get_alpaca_trading_client", return_value=mock_client):
        res = AlpacaUtils.close_position("SWIM")

    assert res["success"] is True
    assert res["order_id"] == "close_order_999"
    mock_client.cancel_order_by_id.assert_called_once_with("order_swim_123")
    mock_client.close_position.assert_called_once_with("SWIM")
