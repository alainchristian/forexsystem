import sys
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
