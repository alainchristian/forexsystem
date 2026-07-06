import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import pytest
from unittest.mock import MagicMock

from src.backtester import Backtester, Trade
from src.execution_logic import calculate_kelly_fraction, dollar_per_pip_per_lot
from config.config import BACKTEST_KELLY_FRACTION_MIN, BACKTEST_KELLY_FRACTION_MAX, SYMBOLS


def _bare_backtester(capital=10000.0):
    """Backtester.__init__ needs a non-empty df; build one directly and
    reset capital/trades so _calculate_position_size can be exercised in
    isolation with a hand-built trade history."""
    import pandas as pd
    df = pd.DataFrame({"close": [1.0, 1.1]})
    bt = Backtester(df, initial_capital=capital)
    return bt


def _fake_trade(pnl):
    t = MagicMock()
    t.pnl = pnl
    return t


def test_calculate_position_size_uses_fixed_risk_before_10_trades():
    bt = _bare_backtester()
    bt.trades = [_fake_trade(10.0)] * 5  # fewer than 10 trades
    size = bt._calculate_position_size(entry_price=100.0)

    risk_dollars = bt.capital * bt.risk_per_trade
    assert size == risk_dollars / 100.0 / 100


def test_calculate_position_size_matches_shared_kelly_formula_with_losses():
    """Regression guard: refactoring _calculate_position_size to call the
    shared calculate_kelly_fraction() must not change backtester's real
    numeric output for the same trade history."""
    bt = _bare_backtester()
    bt.trades = [_fake_trade(50.0)] * 6 + [_fake_trade(-30.0)] * 5  # 11 trades, >10

    size = bt._calculate_position_size(entry_price=100.0)

    win_rate = 6 / 11
    avg_win = 50.0
    avg_loss = 30.0
    expected_kelly = calculate_kelly_fraction(
        win_rate, avg_win, avg_loss,
        min_frac=BACKTEST_KELLY_FRACTION_MIN, max_frac=BACKTEST_KELLY_FRACTION_MAX,
    )
    expected_risk_dollars = bt.capital * expected_kelly
    expected_size = max(0.01, min(100.0, expected_risk_dollars / 100.0 / 100))

    assert size == expected_size


def test_calculate_position_size_maxes_out_kelly_when_no_losses_yet():
    """No losing trades yet -> can't compute avg_loss, so the fraction maxes
    out at BACKTEST_KELLY_FRACTION_MAX rather than dividing by an unknown -
    matches the pre-refactor behavior (0.5 immediately re-clamped to 0.2)."""
    bt = _bare_backtester()
    bt.trades = [_fake_trade(50.0)] * 11  # all wins, >10 trades, zero losses

    size = bt._calculate_position_size(entry_price=100.0)

    expected_risk_dollars = bt.capital * BACKTEST_KELLY_FRACTION_MAX
    expected_size = max(0.01, min(100.0, expected_risk_dollars / 100.0 / 100))
    assert size == expected_size


def _make_trade(entry_price, exit_price, direction='LONG', volume=1.0,
                slippage_pips=1.0, commission=5.0, symbol=None):
    return Trade(
        entry_idx=0, entry_time=datetime(2024, 1, 1),
        entry_price=entry_price,
        exit_idx=1, exit_time=datetime(2024, 1, 1),
        exit_price=exit_price,
        direction=direction, volume=volume,
        slippage_pips=slippage_pips, commission=commission,
        symbol=symbol,
    )


def test_pnl_unchanged_for_symbol_none_default():
    """symbol=None (today's default for the three call sites that don't pass
    one - ensemble.py's demo path, backtester.py's own __main__, and
    tests/test_phase1.py) must reproduce the exact pre-fix numeric output for
    a USD-quoted pair: old formula was (exit-entry)*volume*100000, slippage
    cost = slippage_pips*10*volume."""
    t = _make_trade(entry_price=1.1000, exit_price=1.1050)

    expected_pnl_before_costs = (1.1050 - 1.1000) * 1.0 * 100000
    expected_pnl = expected_pnl_before_costs - (1.0 * 10.0 * 1.0) - 5.0

    assert t.risk_pips == pytest.approx(abs(1.1050 - 1.1000) * 10000)
    assert t.pnl_before_costs == pytest.approx(expected_pnl_before_costs)
    assert t.pnl == pytest.approx(expected_pnl)


def test_pnl_unchanged_for_explicit_non_jpy_symbol():
    """An explicit non-JPY symbol (pip_value=0.0001) must produce the same
    numbers as the symbol=None fallback - proves the SYMBOLS lookup path and
    the None-fallback path agree for USD-quoted pairs."""
    t = _make_trade(entry_price=1.1000, exit_price=1.1050, symbol='EURUSD')

    expected_pnl_before_costs = (1.1050 - 1.1000) * 1.0 * 100000
    expected_pnl = expected_pnl_before_costs - (1.0 * 10.0 * 1.0) - 5.0

    assert t.pnl_before_costs == pytest.approx(expected_pnl_before_costs)
    assert t.pnl == pytest.approx(expected_pnl)


def test_jpy_pair_pnl_converts_through_exchange_rate():
    """Regression guard for the currency-conversion bug: a JPY-quoted pair's
    P&L must be divided through by the entry price (JPY->USD conversion),
    not treated as USD-quoted like every other pair. Old (buggy) formula
    over-counted JPY P&L by a factor equal to the entry price (~150x for
    USDJPY around 150.00) - this test locks in that exact ratio so the bug
    can't silently come back."""
    entry, exit_price = 150.00, 151.00  # 100 pips at pip_value=0.01
    volume = 1.0

    t = _make_trade(entry_price=entry, exit_price=exit_price,
                    volume=volume, slippage_pips=0.0, commission=0.0,
                    symbol='USDJPY')

    pip_value = SYMBOLS['USDJPY']['pip_value']
    dollar_per_pip = dollar_per_pip_per_lot(pip_value, entry)
    pips_moved = (exit_price - entry) / pip_value
    expected_pnl = pips_moved * dollar_per_pip * volume

    assert t.pnl_before_costs == pytest.approx(expected_pnl)

    # What the old, pre-fix code would have computed:
    old_buggy_pnl = (exit_price - entry) * volume * 100000
    assert t.pnl_before_costs != pytest.approx(old_buggy_pnl)

    ratio = old_buggy_pnl / t.pnl_before_costs
    print(f"\nJPY P&L bug magnitude: old(buggy)=${old_buggy_pnl:,.2f} "
          f"new(fixed)=${t.pnl_before_costs:,.2f} ratio={ratio:.1f}x")
    assert ratio == pytest.approx(entry)


def test_jpy_pair_slippage_cost_is_pip_aware():
    """Slippage cost must use the same JPY-aware dollar-per-pip conversion as
    the main P&L, not a flat $10/pip regardless of symbol."""
    entry, exit_price = 150.00, 151.00
    t = _make_trade(entry_price=entry, exit_price=exit_price,
                    volume=1.0, slippage_pips=2.0, commission=0.0,
                    symbol='USDJPY')

    pip_value = SYMBOLS['USDJPY']['pip_value']
    dollar_per_pip = dollar_per_pip_per_lot(pip_value, entry)
    expected_slippage_cost = 2.0 * dollar_per_pip * 1.0

    assert t.pnl == pytest.approx(t.pnl_before_costs - expected_slippage_cost)


def test_backtester_threads_symbol_into_trade_and_slippage():
    """Backtester(symbol=...) must reach the closed Trade (for pip-aware
    P&L) and the exit-price slippage adjustment (previously a hardcoded
    /10000, wrong for JPY pairs where 1 pip = 0.01)."""
    import numpy as np
    import pandas as pd

    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=3, freq="4h"),
        "close": [150.00, 150.50, 151.00],
    })
    bt = Backtester(df, initial_capital=10000.0, slippage_pips=2.0,
                    commission=0.0, symbol='USDJPY')
    signals = np.array([1, 0, 0])  # open LONG at bar 0, held to the end
    bt.backtest(df.reset_index(drop=True), signals)

    assert len(bt.trades) == 1
    trade = bt.trades[0]
    assert trade.symbol == 'USDJPY'

    pip_value = SYMBOLS['USDJPY']['pip_value']
    expected_exit_price = 151.00 - (2.0 * pip_value)  # LONG: slippage subtracted
    assert trade.exit_price == pytest.approx(expected_exit_price)
