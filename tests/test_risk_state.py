import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from src import risk_state


def _mock_conn(fetchone_return=None, raise_on_execute=None):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = fetchone_return
    if raise_on_execute is not None:
        cursor.execute.side_effect = raise_on_execute
    return conn, cursor


def test_load_returns_none_when_no_row_exists():
    """No persisted row yet (genuine first run) must return None, not raise -
    this is what lets main.py fall back to live MT5 equity as the initial
    peak on a real first run."""
    conn, _ = _mock_conn(fetchone_return=None)
    with patch("src.risk_state.db") as mock_db:
        mock_db.get_conn.return_value = conn
        result = risk_state.load()

    assert result is None
    mock_db.put_conn.assert_called_once_with(conn)


def test_load_returns_persisted_state_when_row_exists():
    conn, _ = _mock_conn(fetchone_return=(10500.0, -200.0, date(2026, 7, 6)))
    with patch("src.risk_state.db") as mock_db:
        mock_db.get_conn.return_value = conn
        result = risk_state.load()

    assert result == {
        "peak_equity": 10500.0,
        "daily_pnl": -200.0,
        "daily_pnl_date": date(2026, 7, 6),
    }


def test_load_raises_risk_state_unavailable_when_connection_fails():
    """A DB connect failure must NOT be treated the same as 'no row exists' -
    collapsing the two would let a DB hiccup at restart silently reproduce
    the exact bug this module exists to close (same convention as
    MT5Trader.get_open_positions() distinguishing a failed query from a
    genuinely empty result)."""
    with patch("src.risk_state.db") as mock_db:
        mock_db.get_conn.side_effect = ConnectionError("db unreachable")
        with pytest.raises(risk_state.RiskStateUnavailable):
            risk_state.load()


def test_load_raises_risk_state_unavailable_when_query_fails():
    conn, cursor = _mock_conn(raise_on_execute=Exception("query failed"))
    with patch("src.risk_state.db") as mock_db:
        mock_db.get_conn.return_value = conn
        with pytest.raises(risk_state.RiskStateUnavailable):
            risk_state.load()

    mock_db.put_conn.assert_called_once_with(conn)


def test_save_returns_true_and_commits_on_success():
    conn, cursor = _mock_conn()
    with patch("src.risk_state.db") as mock_db:
        mock_db.get_conn.return_value = conn
        result = risk_state.save(10500.0, -200.0, date(2026, 7, 6))

    assert result is True
    conn.commit.assert_called_once()
    args = cursor.execute.call_args[0][1]
    assert args == (10500.0, -200.0, date(2026, 7, 6))


def test_save_returns_false_and_rolls_back_on_db_error_without_raising():
    """A save failure must never crash a live trade-close flow - callers
    (main.py's _persist_risk_state) only log on a False return, they never
    expect an exception."""
    conn, cursor = _mock_conn(raise_on_execute=Exception("write failed"))
    with patch("src.risk_state.db") as mock_db:
        mock_db.get_conn.return_value = conn
        result = risk_state.save(10500.0, -200.0, date(2026, 7, 6))

    assert result is False
    conn.rollback.assert_called_once()


def test_save_returns_false_when_connection_unavailable():
    with patch("src.risk_state.db") as mock_db:
        mock_db.get_conn.side_effect = ConnectionError("db unreachable")
        result = risk_state.save(10500.0, -200.0, date(2026, 7, 6))

    assert result is False
