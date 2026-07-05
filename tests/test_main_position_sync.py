import sys
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from src.main import TradingSystem
from src.risk_manager import RiskManager, RiskConfig


def _bare_trading_system(open_positions: dict):
    """Build a TradingSystem instance without running __init__ (which
    connects to Postgres/Redis/MT5/Telegram and loads the ML models) -
    only _sync_closed_positions/_reconcile_positions and the attributes
    they touch are under test here."""
    system = object.__new__(TradingSystem)
    system.risk_mgr = RiskManager(RiskConfig(account_equity=10000.0))
    system.telegram = AsyncMock()
    system.trader = MagicMock()
    system.trader.mt5_initialized = True
    system.trader.open_positions = open_positions
    system.trader.get_closed_pnl = AsyncMock(return_value=10.0)
    system.trader.queue_pnl_reconciliation = MagicMock()
    return system


def _sample_position(symbol="EURUSD"):
    return {"symbol": symbol, "direction": 1, "volume": 0.1, "entry": 1.10}


def test_sync_closed_positions_does_nothing_when_mt5_query_fails():
    """get_open_positions() returning None (a failed query) must not be
    treated as 'zero positions open' - that previously made every real,
    still-open position look closed via SL/TP, popped it from tracking,
    and queued a P&L reconciliation that could never succeed because the
    position never actually closed."""
    open_positions = {111: _sample_position("EURUSD"), 222: _sample_position("GBPUSD")}
    system = _bare_trading_system(open_positions)
    system.trader.get_open_positions = MagicMock(return_value=None)

    asyncio.run(system._sync_closed_positions())

    assert system.trader.open_positions == {111: _sample_position("EURUSD"), 222: _sample_position("GBPUSD")}
    system.trader.queue_pnl_reconciliation.assert_not_called()
    system.telegram.send_alert.assert_not_called()


def test_sync_closed_positions_still_detects_genuine_close():
    """Regression guard: a ticket genuinely missing from a real (non-None)
    live positions list must still be treated as closed."""
    open_positions = {111: _sample_position("EURUSD"), 222: _sample_position("GBPUSD")}
    system = _bare_trading_system(open_positions)
    # Only 222 is still live; 111 genuinely closed.
    system.trader.get_open_positions = MagicMock(return_value=[MagicMock(ticket=222)])

    asyncio.run(system._sync_closed_positions())

    assert 111 not in system.trader.open_positions
    assert 222 in system.trader.open_positions


def test_reconcile_positions_handles_mt5_query_failure():
    """Startup reconciliation must not crash (or wipe tracking) if MT5
    can't be queried yet."""
    system = _bare_trading_system({})
    system.trader.get_open_positions = MagicMock(return_value=None)

    asyncio.run(system._reconcile_positions())

    assert system.trader.open_positions == {}


def test_trailing_stop_offset_differs_correctly_between_jpy_and_non_jpy():
    """The same TRAILING_STOP_LOCK_PIPS constant must produce a different
    price-unit offset for a JPY pair (pip_value=0.01) vs a non-JPY pair
    (pip_value=0.0001), proving the fix is genuinely pip-aware rather than
    coincidentally matching one pair type."""
    from config.config import SYMBOLS, TRAILING_STOP_LOCK_PIPS

    async def _run(symbol, entry, sl, current_price):
        order_id = 900
        position = {
            "symbol": symbol, "direction": 1, "volume": 1.0,
            "entry": entry, "sl": sl, "opened_at": None,
        }
        system = _bare_trading_system({order_id: position})
        system._redis_available = False
        system._price_cache = {symbol: current_price}
        system.trader.modify_position_sl = AsyncMock(return_value=True)
        with patch("redis.Redis", side_effect=ConnectionError("no redis in tests")):
            await system.update_trailing_stops()
        return system.trader.modify_position_sl

    # EURUSD: entry 1.1000, sl 1.0950 (50 pip risk), price rallies to 1.1300
    # (300 pip profit >> 2x initial risk) so the trailing stop must engage.
    mock_eur = asyncio.run(_run("EURUSD", entry=1.1000, sl=1.0950, current_price=1.1300))
    mock_eur.assert_awaited_once()
    eur_new_sl = mock_eur.call_args[0][1]
    eur_offset = eur_new_sl - 1.1000

    # USDJPY: entry 150.00, sl 149.00 (100 pip risk), price rallies to 152.50
    # (250 pip profit >> 2x initial risk).
    mock_jpy = asyncio.run(_run("USDJPY", entry=150.00, sl=149.00, current_price=152.50))
    mock_jpy.assert_awaited_once()
    jpy_new_sl = mock_jpy.call_args[0][1]
    jpy_offset = jpy_new_sl - 150.00

    assert eur_offset == pytest.approx(TRAILING_STOP_LOCK_PIPS * SYMBOLS["EURUSD"]["pip_value"])
    assert jpy_offset == pytest.approx(TRAILING_STOP_LOCK_PIPS * SYMBOLS["USDJPY"]["pip_value"])
    assert eur_offset != jpy_offset
    # Old flat 0.005 value must no longer appear as either offset.
    assert eur_offset != pytest.approx(0.005)
    assert jpy_offset != pytest.approx(0.005)


def test_update_trailing_stops_awaits_modify_position_sl():
    """modify_position_sl is now async (bridge mode verifies the EA's result
    instead of blindly succeeding) - update_trailing_stops must actually
    await it, not call it as if it still returned a bool synchronously."""
    order_id = 555
    position = {
        "symbol": "EURUSD", "direction": 1, "volume": 1.0,
        "entry": 1.1000, "sl": 1.0950, "opened_at": None,
    }
    system = _bare_trading_system({order_id: position})
    system._redis_available = False
    system._price_cache = {"EURUSD": 1.1200}  # profit = 0.02 * 1.0 = 0.02 > 2*initial_risk (0.01)
    system.trader.modify_position_sl = AsyncMock(return_value=True)

    # update_trailing_stops() tries a real Redis connection first; force it
    # to fail fast instead of hitting socket_connect_timeout against a
    # Redis that isn't running in the test environment.
    with patch("redis.Redis", side_effect=ConnectionError("no redis in tests")):
        asyncio.run(system.update_trailing_stops())

    system.trader.modify_position_sl.assert_awaited_once()
