import sys
import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from src.mt5_trader import MT5Trader
from src.risk_manager import RiskManager, RiskConfig

# NOTE: pytest-asyncio is not a dependency of this project, so async tests are
# driven directly with asyncio.run() instead of @pytest.mark.asyncio — that
# decorator silently no-ops the coroutine (never awaited) when the plugin
# isn't installed, which is exactly how test_submit_order_success and
# test_submit_order_blocked_by_risk went stale below without ever failing CI.

@pytest.fixture
def mock_dependencies():
    risk_config = RiskConfig(account_equity=10000.0)
    risk_mgr = RiskManager(risk_config)
    risk_mgr.can_open_trade = MagicMock(return_value={'valid': True, 'reason': 'OK'})
    risk_mgr.validate_trade_setup = MagicMock(return_value={'valid': True, 'ratio': 2.0})

    telegram = AsyncMock()

    return risk_mgr, telegram

@patch('src.mt5_trader.mt5')
def test_submit_order_success(mock_mt5, mock_dependencies):
    """Test successful order submission and verify request dictionary format"""
    risk_mgr, telegram = mock_dependencies

    # Setup mocks
    mock_mt5.initialize.return_value = True
    mock_mt5.ORDER_TYPE_BUY = 0
    mock_mt5.ORDER_TYPE_SELL = 1
    mock_mt5.TRADE_ACTION_DEAL = 1
    mock_mt5.TRADE_RETCODE_DONE = 10009

    mock_result = MagicMock()
    mock_result.retcode = 10009
    mock_result.order = 12345
    mock_mt5.order_send.return_value = mock_result
    mock_mt5.symbol_info_tick.return_value = None  # skip the live-spread check
    mock_mt5.order_calc_margin.return_value = 0     # skip the margin-reduction check

    # account_info() is polled in a loop during initialize() — give it a
    # real numeric balance so the f-string formatting doesn't blow up and
    # fall through to bridge mode (which would hit the real filesystem).
    mock_account = MagicMock(balance=10000.0, currency="USD")
    mock_mt5.account_info.return_value = mock_account

    # We must patch MT5_AVAILABLE globally in mt5_trader
    with patch('src.mt5_trader.MT5_AVAILABLE', True):
        trader = MT5Trader(123, 'pass', 'server', risk_mgr, telegram)
        trader.initialize()
        assert trader.mt5_initialized is True
        assert trader._bridge_mode is False

        order_id = asyncio.run(trader.submit_order(
            symbol="EURUSD",
            direction=1,  # BUY
            volume=1.5,
            entry_price=1.1000,
            stop_loss=1.0980,
            take_profit=1.1040
        ))

        assert order_id == 12345
        assert 12345 in trader.open_positions

        # Verify the MT5 request dictionary was built correctly
        request_arg = mock_mt5.order_send.call_args[0][0]
        assert request_arg['symbol'] == "EURUSD"
        assert request_arg['volume'] == 1.5
        assert request_arg['type'] == mock_mt5.ORDER_TYPE_BUY
        assert request_arg['sl'] == 1.0980
        assert request_arg['tp'] == 1.1040
        assert request_arg['price'] == 1.1000

        # Verify telegram was notified
        telegram.send_alert.assert_called_once()

@patch('src.mt5_trader.mt5')
def test_submit_order_blocked_by_risk(mock_mt5, mock_dependencies):
    """Test that Risk Manager can block trades before sending to MT5"""
    risk_mgr, telegram = mock_dependencies
    risk_mgr.can_open_trade.return_value = {'valid': False, 'reason': 'Risk limits exceeded'}

    mock_mt5.initialize.return_value = True
    mock_mt5.account_info.return_value = MagicMock(balance=10000.0, currency="USD")

    with patch('src.mt5_trader.MT5_AVAILABLE', True):
        trader = MT5Trader(123, 'pass', 'server', risk_mgr, telegram)
        trader.initialize()
        assert trader.mt5_initialized is True
        assert trader._bridge_mode is False

        order_id = asyncio.run(trader.submit_order("EURUSD", 1, 1.0, 1.1, 1.0, 1.2))

        assert order_id is None
        mock_mt5.order_send.assert_not_called()  # It never hit the API
        telegram.send_alert.assert_called_once_with("⛔ Trade blocked: Risk limits exceeded")


# ----------------------------------------------------------------------------
# get_closed_pnl / close_position — P&L lookup retry loop
# ----------------------------------------------------------------------------

def test_get_closed_pnl_retries_until_deal_found(mock_dependencies):
    """history_deals_get may not have the deal on the first poll — the retry
    loop should keep polling (with backoff) until it shows up."""
    risk_mgr, telegram = mock_dependencies

    with patch('src.mt5_trader.MT5_AVAILABLE', True), patch('src.mt5_trader.mt5') as mock_mt5:
        trader = MT5Trader(123, 'pass', 'server', risk_mgr, telegram)
        trader._bridge_mode = False

        deal = MagicMock()
        deal.position_id = 777
        deal.profit = 15.25
        deal.entry = 1  # DEAL_ENTRY_OUT - a real close, not the opening deal
        # Empty on the first two polls, deal appears on the third.
        mock_mt5.history_deals_get.side_effect = [[], [], [deal]]

        pnl = asyncio.run(trader.get_closed_pnl(777, max_attempts=5, base_delay=0.01))

        assert pnl == 15.25
        assert mock_mt5.history_deals_get.call_count == 3


def test_get_closed_pnl_ignores_entry_deal_and_keeps_waiting_for_exit(mock_dependencies):
    """Regression test: a position's entry (open) deal is already in MT5
    history the moment it's closed, well before the closing deal registers.
    Treating "any matching deal found" as success returned the entry deal's
    profit=0.0 as if it were the final P&L - in production this logged a
    real -$62.27 close as $+0.00 and fed that fake zero into daily P&L.
    get_closed_pnl must keep retrying until an exit-type deal (entry != 0)
    shows up, not stop at the first (stale) match.
    """
    risk_mgr, telegram = mock_dependencies

    with patch('src.mt5_trader.MT5_AVAILABLE', True), patch('src.mt5_trader.mt5') as mock_mt5:
        trader = MT5Trader(123, 'pass', 'server', risk_mgr, telegram)
        trader._bridge_mode = False

        entry_deal = MagicMock(position_id=555, profit=0.0, entry=0)  # DEAL_ENTRY_IN
        exit_deal = MagicMock(position_id=555, profit=-62.27, entry=1)  # DEAL_ENTRY_OUT

        # First two polls only see the stale entry deal; the real close
        # shows up on the third poll alongside it.
        mock_mt5.history_deals_get.side_effect = [
            [entry_deal],
            [entry_deal],
            [entry_deal, exit_deal],
        ]

        pnl = asyncio.run(trader.get_closed_pnl(555, max_attempts=5, base_delay=0.01))

        assert pnl == -62.27
        assert mock_mt5.history_deals_get.call_count == 3


def test_get_closed_pnl_returns_none_when_deal_never_appears(mock_dependencies):
    """If the deal never shows up after exhausting retries, the function must
    return None (not 0.0) so callers can tell 'unknown' from 'confirmed zero'."""
    risk_mgr, telegram = mock_dependencies

    with patch('src.mt5_trader.MT5_AVAILABLE', True), patch('src.mt5_trader.mt5') as mock_mt5:
        trader = MT5Trader(123, 'pass', 'server', risk_mgr, telegram)
        trader._bridge_mode = False
        mock_mt5.history_deals_get.return_value = []

        pnl = asyncio.run(trader.get_closed_pnl(888, max_attempts=3, base_delay=0.01))

        assert pnl is None
        assert mock_mt5.history_deals_get.call_count == 3


def test_close_position_does_not_feed_zero_pnl_to_risk_manager_on_lookup_failure(mock_dependencies):
    """A permanent P&L-lookup failure must not silently update daily P&L with
    0.0 — that corrupts the daily loss circuit breaker."""
    risk_mgr, telegram = mock_dependencies
    risk_mgr.update_daily_pnl = MagicMock(wraps=risk_mgr.update_daily_pnl)

    with patch('src.mt5_trader.MT5_AVAILABLE', True), \
         patch('src.mt5_trader.mt5') as mock_mt5, \
         patch('src.mt5_trader.asyncio.sleep', new=AsyncMock()):
        trader = MT5Trader(123, 'pass', 'server', risk_mgr, telegram)
        trader.mt5_initialized = True
        trader._bridge_mode = False
        trader.open_positions[321] = {
            "symbol": "EURUSD", "direction": 1, "volume": 0.1,
            "entry": 1.10, "sl": 1.09, "tp": 1.12,
            "opened_at": datetime.now(), "order_id": 321, "confidence": 0.6,
        }

        mock_mt5.ORDER_TYPE_SELL = 1
        mock_mt5.ORDER_TYPE_BUY = 0
        mock_mt5.TRADE_ACTION_DEAL = 1
        mock_mt5.TRADE_RETCODE_DONE = 10009
        mock_mt5.symbol_info_tick.return_value = MagicMock(bid=1.105, ask=1.106)
        mock_mt5.order_send.return_value = MagicMock(retcode=10009)
        mock_mt5.history_deals_get.return_value = []  # deal never registers

        ok = asyncio.run(trader.close_position(321, reason="Manual"))

    assert ok is True
    risk_mgr.update_daily_pnl.assert_not_called()
    assert trader.trade_log[-1]["pnl"] is None
    assert 321 not in trader.open_positions


# ----------------------------------------------------------------------------
# Ranked Replacement guardrails (min hold time + min confidence gap)
# ----------------------------------------------------------------------------

def _trader_with_one_open_position(hold_minutes, low_confidence,
                                    min_hold=10.0, min_gap=0.07, max_open_trades=1):
    """Build a trader whose only open slot is filled by a low-confidence
    GBPUSD position opened `hold_minutes` ago, so a new signal on a
    different symbol will hit the Ranked Replacement branch."""
    risk_config = RiskConfig(
        account_equity=10000.0,
        max_open_trades=max_open_trades,
        min_replacement_hold_minutes=min_hold,
        min_replacement_confidence_gap=min_gap,
    )
    risk_mgr = RiskManager(risk_config)
    risk_mgr.validate_trade_setup = MagicMock(return_value={'valid': True, 'ratio': 2.0})
    telegram = AsyncMock()

    trader = MT5Trader(123, 'pass', 'server', risk_mgr, telegram)
    trader.mt5_initialized = True
    trader._bridge_mode = False

    lowest_id = 111
    trader.open_positions[lowest_id] = {
        "symbol": "GBPUSD", "direction": 1, "volume": 0.1,
        "entry": 1.25, "sl": 1.24, "tp": 1.27,
        "opened_at": datetime.now() - timedelta(minutes=hold_minutes),
        "order_id": lowest_id, "confidence": low_confidence,
    }

    async def fake_close(order_id, reason="Manual"):
        trader.open_positions.pop(order_id, None)
        return True
    trader.close_position = AsyncMock(side_effect=fake_close)

    return trader, lowest_id


def _patch_mt5_for_order_submit(mock_mt5, order_id=999, lowest_id=None, lowest_profit=0.0):
    mock_mt5.symbol_info_tick.return_value = None   # skip spread check
    mock_mt5.account_info.return_value = None        # skip margin check
    mock_mt5.ORDER_TYPE_BUY = 0
    mock_mt5.ORDER_TYPE_SELL = 1
    mock_mt5.TRADE_ACTION_DEAL = 1
    mock_mt5.TRADE_RETCODE_DONE = 10009
    mock_mt5.order_send.return_value = MagicMock(retcode=10009, order=order_id)
    # get_open_positions() -> mt5.positions_get() backs the live-profit check
    # the Ranked Replacement guardrail uses.
    if lowest_id is not None:
        mock_mt5.positions_get.return_value = [MagicMock(ticket=lowest_id, profit=lowest_profit)]
    else:
        mock_mt5.positions_get.return_value = []


def test_ranked_replacement_rejected_when_hold_time_too_short():
    """Large confidence gap alone isn't enough — the existing position must
    also have been open long enough."""
    trader, lowest_id = _trader_with_one_open_position(
        hold_minutes=2.0, low_confidence=0.55, min_hold=10.0, min_gap=0.05
    )
    with patch('src.mt5_trader.MT5_AVAILABLE', True), patch('src.mt5_trader.mt5') as mock_mt5:
        _patch_mt5_for_order_submit(mock_mt5, lowest_id=lowest_id, lowest_profit=0.0)
        order_id = asyncio.run(trader.submit_order(
            symbol="EURUSD", direction=1, volume=0.1,
            entry_price=1.10, stop_loss=1.09, take_profit=1.12,
            confidence=0.90,  # gap = 0.35, well above min_gap
        ))

    assert order_id is None
    trader.close_position.assert_not_called()
    assert lowest_id in trader.open_positions


def test_ranked_replacement_rejected_when_confidence_gap_too_small():
    """Long hold time alone isn't enough — the new signal must also clear
    the minimum confidence gap over the position being bumped."""
    trader, lowest_id = _trader_with_one_open_position(
        hold_minutes=30.0, low_confidence=0.60, min_hold=10.0, min_gap=0.10
    )
    with patch('src.mt5_trader.MT5_AVAILABLE', True), patch('src.mt5_trader.mt5') as mock_mt5:
        _patch_mt5_for_order_submit(mock_mt5, lowest_id=lowest_id, lowest_profit=0.0)
        order_id = asyncio.run(trader.submit_order(
            symbol="EURUSD", direction=1, volume=0.1,
            entry_price=1.10, stop_loss=1.09, take_profit=1.12,
            confidence=0.62,  # gap = 0.02, below min_gap
        ))

    assert order_id is None
    trader.close_position.assert_not_called()
    assert lowest_id in trader.open_positions


def test_ranked_replacement_rejected_when_position_is_losing():
    """Long enough hold time + large enough confidence gap still isn't
    enough — a position currently at a loss must never be closed purely to
    make room for a new signal."""
    trader, lowest_id = _trader_with_one_open_position(
        hold_minutes=30.0, low_confidence=0.55, min_hold=10.0, min_gap=0.05
    )
    with patch('src.mt5_trader.MT5_AVAILABLE', True), patch('src.mt5_trader.mt5') as mock_mt5:
        _patch_mt5_for_order_submit(mock_mt5, lowest_id=lowest_id, lowest_profit=-62.27)
        order_id = asyncio.run(trader.submit_order(
            symbol="EURUSD", direction=1, volume=0.1,
            entry_price=1.10, stop_loss=1.09, take_profit=1.12,
            confidence=0.90,  # gap = 0.35, well above min_gap
        ))

    assert order_id is None
    trader.close_position.assert_not_called()
    assert lowest_id in trader.open_positions


def test_ranked_replacement_rejected_when_live_profit_unknown():
    """If the live P&L lookup fails, fail safe and refuse the replacement -
    don't risk closing a position based on missing data."""
    trader, lowest_id = _trader_with_one_open_position(
        hold_minutes=30.0, low_confidence=0.55, min_hold=10.0, min_gap=0.05
    )
    with patch('src.mt5_trader.MT5_AVAILABLE', True), patch('src.mt5_trader.mt5') as mock_mt5:
        _patch_mt5_for_order_submit(mock_mt5, lowest_id=None)  # positions_get() -> [] -> ticket not found
        order_id = asyncio.run(trader.submit_order(
            symbol="EURUSD", direction=1, volume=0.1,
            entry_price=1.10, stop_loss=1.09, take_profit=1.12,
            confidence=0.90,
        ))

    assert order_id is None
    trader.close_position.assert_not_called()
    assert lowest_id in trader.open_positions


def test_ranked_replacement_accepted_when_all_conditions_pass():
    """Long enough hold time + large enough confidence gap + not currently
    losing => replacement proceeds and the new order is submitted."""
    trader, lowest_id = _trader_with_one_open_position(
        hold_minutes=30.0, low_confidence=0.55, min_hold=10.0, min_gap=0.05
    )
    with patch('src.mt5_trader.MT5_AVAILABLE', True), patch('src.mt5_trader.mt5') as mock_mt5:
        _patch_mt5_for_order_submit(mock_mt5, order_id=999, lowest_id=lowest_id, lowest_profit=12.50)
        order_id = asyncio.run(trader.submit_order(
            symbol="EURUSD", direction=1, volume=0.1,
            entry_price=1.10, stop_loss=1.09, take_profit=1.12,
            confidence=0.70,  # gap = 0.15, above min_gap
        ))

    assert order_id == 999
    trader.close_position.assert_called_once_with(lowest_id, "Ranked Replacement")
    assert lowest_id not in trader.open_positions
    assert 999 in trader.open_positions


def test_no_replacement_triggered_when_open_slot_available():
    """Existing behaviour: if max_open_trades hasn't been reached, the
    Ranked Replacement branch must not engage at all."""
    trader, lowest_id = _trader_with_one_open_position(
        hold_minutes=30.0, low_confidence=0.55, min_hold=10.0, min_gap=0.05,
        max_open_trades=5,
    )
    with patch('src.mt5_trader.MT5_AVAILABLE', True), patch('src.mt5_trader.mt5') as mock_mt5:
        _patch_mt5_for_order_submit(mock_mt5, order_id=555)
        order_id = asyncio.run(trader.submit_order(
            symbol="EURUSD", direction=1, volume=0.1,
            entry_price=1.10, stop_loss=1.09, take_profit=1.12,
            confidence=0.90,
        ))

    assert order_id == 555
    trader.close_position.assert_not_called()


def test_no_replacement_triggered_for_per_symbol_cap():
    """Per-symbol/volume caps aren't resolved by closing an unrelated
    position, so Ranked Replacement must only engage for the global
    portfolio cap - not "Max trades for X" / "Max volume for X" reasons,
    even when hold time and confidence gap would otherwise clear it."""
    trader, lowest_id = _trader_with_one_open_position(
        hold_minutes=30.0, low_confidence=0.55, min_hold=10.0, min_gap=0.05,
        max_open_trades=5,  # plenty of global room
    )
    with patch('src.mt5_trader.MT5_AVAILABLE', True), patch('src.mt5_trader.mt5') as mock_mt5:
        _patch_mt5_for_order_submit(mock_mt5)
        # Same symbol as the existing position -> trips max_trades_per_symbol
        # (1), not the global cap.
        order_id = asyncio.run(trader.submit_order(
            symbol="GBPUSD", direction=1, volume=0.1,
            entry_price=1.10, stop_loss=1.09, take_profit=1.12,
            confidence=0.90,
        ))

    assert order_id is None
    trader.close_position.assert_not_called()
    assert lowest_id in trader.open_positions


# ----------------------------------------------------------------------------
# reconcile_pending_pnl — backfill job for permanently-failed P&L lookups
# ----------------------------------------------------------------------------

def test_reconcile_pending_pnl_backfills_once_deal_appears(mock_dependencies):
    """Once the deal shows up in MT5 history on a later tick, the queued
    reconciliation should backfill daily P&L and the trade_log entry, then
    stop being retried."""
    risk_mgr, telegram = mock_dependencies

    with patch('src.mt5_trader.MT5_AVAILABLE', True), patch('src.mt5_trader.mt5') as mock_mt5:
        trader = MT5Trader(123, 'pass', 'server', risk_mgr, telegram)
        trader._bridge_mode = False
        trader.trade_log.append({
            "order_id": 555, "symbol": "EURUSD", "entry": 1.10, "exit": 1.105,
            "volume": 0.1, "pnl": None, "reason": "Manual", "duration": 0.1,
        })
        trader.queue_pnl_reconciliation(555, "EURUSD", "Manual")

        deal = MagicMock(position_id=555, profit=8.0, entry=1)
        mock_mt5.history_deals_get.return_value = [deal]

        asyncio.run(trader.reconcile_pending_pnl())

    assert 555 not in trader.pending_pnl
    assert trader.trade_log[-1]["pnl"] == 8.0
    assert risk_mgr.daily_pnl == 8.0
    telegram.send_alert.assert_called_once()
    assert "reconciled" in telegram.send_alert.call_args[0][0].lower()


def test_reconcile_pending_pnl_alerts_loudly_on_repeated_failure(mock_dependencies):
    """A ticket that stays unconfirmed must trigger a distinct loud alert on
    the configured cadence, not just a quiet log line, and must never call
    update_daily_pnl(0.0) while it's stuck."""
    risk_mgr, telegram = mock_dependencies
    risk_mgr.update_daily_pnl = MagicMock(wraps=risk_mgr.update_daily_pnl)

    with patch('src.mt5_trader.MT5_AVAILABLE', True), \
         patch('src.mt5_trader.mt5') as mock_mt5, \
         patch('src.mt5_trader.asyncio.sleep', new=AsyncMock()):
        trader = MT5Trader(123, 'pass', 'server', risk_mgr, telegram)
        trader._bridge_mode = False
        trader.queue_pnl_reconciliation(777, "GBPUSD", "SL/TP hit")
        mock_mt5.history_deals_get.return_value = []  # deal never appears

        # PNL_RECONCILE_ALERT_INTERVAL is 5 — run enough ticks to cross two
        # alert boundaries (attempt 1 and attempt 6).
        for _ in range(6):
            asyncio.run(trader.reconcile_pending_pnl())

    assert 777 in trader.pending_pnl
    assert trader.pending_pnl[777]["attempts"] == 6
    risk_mgr.update_daily_pnl.assert_not_called()
    assert telegram.send_alert.call_count == 2
    for call in telegram.send_alert.call_args_list:
        assert "UNCONFIRMED" in call.args[0]
