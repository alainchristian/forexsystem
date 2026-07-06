import sys
import asyncio
from datetime import datetime, timedelta
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
    system._price_cache = {}
    system._price_cache_updated_at = {}
    system._price_cache_last_alert = {}
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
        system.trader.get_current_price = MagicMock(return_value=current_price)
        system.trader.modify_position_sl = AsyncMock(return_value=True)
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
    system.trader.get_current_price = MagicMock(return_value=1.1200)  # profit = 0.02*1.0 > 2*initial_risk (0.01)
    system.trader.modify_position_sl = AsyncMock(return_value=True)

    asyncio.run(system.update_trailing_stops())

    system.trader.modify_position_sl.assert_awaited_once()


def test_update_trailing_stops_alerts_when_price_never_cached_past_threshold(caplog):
    """get_current_price() failing from the start (no prior successful cache
    write - e.g. bridge mode with no tick data, or MT5 IPC returning no tick)
    with an open position whose age already exceeds
    PRICE_CACHE_STALE_ALERT_MINUTES must both log distinctly per-symbol and
    fire the repeating Telegram alert - trailing-stop protection is silently
    inactive otherwise."""
    from config.config import PRICE_CACHE_STALE_ALERT_MINUTES

    order_id = 777
    position = {
        "symbol": "EURUSD", "direction": 1, "volume": 1.0,
        "entry": 1.1000, "sl": 1.0950,
        "opened_at": datetime.now() - timedelta(minutes=PRICE_CACHE_STALE_ALERT_MINUTES + 5),
    }
    system = _bare_trading_system({order_id: position})
    system.trader.get_current_price = MagicMock(return_value=None)  # never successfully fetched

    import logging
    with caplog.at_level(logging.ERROR):
        asyncio.run(system.update_trailing_stops())

    system.telegram.send_alert.assert_awaited_once()
    alert_msg = system.telegram.send_alert.call_args[0][0]
    assert "EURUSD" in alert_msg
    assert "NO PRICE DATA" in alert_msg
    assert any("EURUSD" in record.message for record in caplog.records)


def test_update_trailing_stops_no_alert_before_threshold():
    """A position that just opened (well under PRICE_CACHE_STALE_ALERT_MINUTES)
    with no fetched price yet must not alert - proves this is a threshold,
    not an immediate page on every single failed tick fetch."""
    order_id = 778
    position = {
        "symbol": "EURUSD", "direction": 1, "volume": 1.0,
        "entry": 1.1000, "sl": 1.0950,
        "opened_at": datetime.now(),
    }
    system = _bare_trading_system({order_id: position})
    system.trader.get_current_price = MagicMock(return_value=None)

    asyncio.run(system.update_trailing_stops())

    system.telegram.send_alert.assert_not_awaited()


# ----------------------------------------------------------------------------
# _sync_account_and_risk_state — persisted peak_equity/daily_pnl surviving a
# restart (see src/risk_state.py). These exercise a genuinely separate
# risk_state.load() call, not just object construction, to prove the restored
# values actually come from "persistence", not from the RiskManager default.
# ----------------------------------------------------------------------------

def _system_for_risk_state_sync(equity=9000.0, initial_capital=10000.0):
    system = _bare_trading_system({})
    system.config = {"initial_capital": initial_capital}
    system.trader.get_account_info = MagicMock(return_value={"equity": equity})
    return system


def test_sync_restores_persisted_peak_equity_higher_than_live_equity():
    """A peak recorded before a crash (12000) must survive a restart even
    though live equity is now lower (9000, e.g. after a drawdown) - this is
    the exact scenario the bug report describes: without this fix, peak_equity
    would silently reset to 9000 and the drawdown breaker would read 0%
    instead of the real ~25%."""
    system = _system_for_risk_state_sync(equity=9000.0)
    persisted = {"peak_equity": 12000.0, "daily_pnl": -300.0, "daily_pnl_date": datetime.utcnow().date()}

    with patch("src.main.risk_state") as mock_risk_state:
        mock_risk_state.load.return_value = persisted
        asyncio.run(system._sync_account_and_risk_state())

    assert system.risk_mgr.config.account_equity == 9000.0
    assert system.risk_mgr.peak_equity == 12000.0
    assert system.risk_mgr.daily_pnl == -300.0


def test_sync_daily_pnl_survives_same_day_restart():
    system = _system_for_risk_state_sync(equity=9700.0)
    persisted = {"peak_equity": 10000.0, "daily_pnl": -450.0, "daily_pnl_date": datetime.utcnow().date()}

    with patch("src.main.risk_state") as mock_risk_state:
        mock_risk_state.load.return_value = persisted
        asyncio.run(system._sync_account_and_risk_state())

    assert system.risk_mgr.daily_pnl == -450.0


def test_sync_ignores_stale_daily_pnl_date_from_a_previous_day():
    """A persisted daily_pnl from a prior day (the reset window was missed
    while the bot was down) must NOT be restored - it should be treated as
    expired, self-healing the missed reset instead of carrying yesterday's
    P&L forward."""
    system = _system_for_risk_state_sync(equity=9700.0)
    yesterday = (datetime.utcnow() - timedelta(days=1)).date()
    persisted = {"peak_equity": 10000.0, "daily_pnl": -450.0, "daily_pnl_date": yesterday}

    with patch("src.main.risk_state") as mock_risk_state:
        mock_risk_state.load.return_value = persisted
        asyncio.run(system._sync_account_and_risk_state())

    assert system.risk_mgr.daily_pnl == 0.0  # unchanged from construction default


def test_sync_first_run_uses_live_equity_as_peak_when_no_persisted_state():
    system = _system_for_risk_state_sync(equity=9700.0)

    with patch("src.main.risk_state") as mock_risk_state:
        mock_risk_state.load.return_value = None
        asyncio.run(system._sync_account_and_risk_state())

    assert system.risk_mgr.peak_equity == 9700.0


def test_sync_alerts_and_falls_back_when_risk_state_db_unavailable():
    """A DB outage at exactly the restart moment must not silently reproduce
    the original bug - it should alert loudly (matching the pending_pnl /
    price-cache alert pattern) rather than pretend nothing is wrong."""
    from src import risk_state as real_risk_state

    system = _system_for_risk_state_sync(equity=9700.0)

    with patch("src.main.risk_state") as mock_risk_state:
        mock_risk_state.RiskStateUnavailable = real_risk_state.RiskStateUnavailable
        mock_risk_state.load.side_effect = real_risk_state.RiskStateUnavailable("db down")
        asyncio.run(system._sync_account_and_risk_state())

    system.telegram.send_alert.assert_awaited_once()
    assert "DB unavailable" in system.telegram.send_alert.call_args[0][0]
    assert system.risk_mgr.peak_equity == 9700.0  # falls back to live equity
