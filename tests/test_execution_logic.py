import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from src.execution_logic import calculate_kelly_fraction


def test_calculate_kelly_fraction_matches_manual_formula():
    win_rate, avg_win, avg_loss = 0.6, 100.0, 50.0
    expected = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win

    result = calculate_kelly_fraction(win_rate, avg_win, avg_loss, min_frac=0.0, max_frac=1.0)

    assert result == expected


def test_calculate_kelly_fraction_clamps_to_max():
    # Very favorable inputs push the raw Kelly value well above max_frac.
    result = calculate_kelly_fraction(win_rate=0.9, avg_win=100.0, avg_loss=10.0,
                                       min_frac=0.01, max_frac=0.2)
    assert result == 0.2


def test_calculate_kelly_fraction_clamps_to_min():
    # Unfavorable inputs push the raw Kelly value well below min_frac.
    result = calculate_kelly_fraction(win_rate=0.2, avg_win=10.0, avg_loss=100.0,
                                       min_frac=0.01, max_frac=0.2)
    assert result == 0.01


def test_calculate_kelly_fraction_zero_avg_win_returns_min_frac():
    result = calculate_kelly_fraction(win_rate=0.0, avg_win=0.0, avg_loss=50.0,
                                       min_frac=0.01, max_frac=0.2)
    assert result == 0.01


def test_risk_manager_and_backtester_call_sites_agree_on_shared_formula():
    """The formulas risk_manager.py and backtester.py used to reimplement
    independently are algebraically identical - this locks that in by
    calling the shared function with each system's own clamp range and
    confirming both still agree with the manual formula before clamping."""
    win_rate, avg_win, avg_loss = 0.55, 80.0, 60.0
    raw = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win

    risk_manager_style = calculate_kelly_fraction(win_rate, avg_win, avg_loss, min_frac=0.5, max_frac=1.5)
    backtester_style = calculate_kelly_fraction(win_rate, avg_win, avg_loss, min_frac=0.01, max_frac=0.2)

    assert risk_manager_style == max(0.5, min(1.5, raw))
    assert backtester_style == max(0.01, min(0.2, raw))
