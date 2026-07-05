"""Shared position-sizing math used by both live risk management
(risk_manager.py) and the backtester (backtester.py), so the two never
drift into separate reimplementations of the same formula again."""


def calculate_kelly_fraction(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    min_frac: float,
    max_frac: float,
) -> float:
    """Kelly fraction f* = (win_rate*avg_win - (1-win_rate)*avg_loss) / avg_win,
    clamped to [min_frac, max_frac].

    avg_win and avg_loss are both expected as positive magnitudes. Returns
    min_frac if avg_win <= 0 (no meaningful edge data / avoids division by
    zero) rather than raising.
    """
    if avg_win <= 0:
        return min_frac
    kelly = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
    return max(min_frac, min(max_frac, kelly))
