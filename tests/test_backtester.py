import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from unittest.mock import MagicMock

from src.backtester import Backtester
from src.execution_logic import calculate_kelly_fraction
from config.config import BACKTEST_KELLY_FRACTION_MIN, BACKTEST_KELLY_FRACTION_MAX


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
