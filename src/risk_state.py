"""Persists RiskManager's account-level circuit-breaker state (peak_equity,
daily_pnl) across restarts, using the shared db.py connection pool.

Without this, main.py re-syncs peak_equity to whatever MT5 reports as
current equity at startup and daily_pnl starts at 0.0 on every fresh
RiskManager() construction - a crash/restart silently erases both
circuit breakers' memory of how bad things got, exactly on the event
most likely to correlate with real trouble.
"""

from datetime import date
from typing import Optional, TypedDict

from src import db


class RiskState(TypedDict):
    peak_equity: float
    daily_pnl: float
    daily_pnl_date: date


class RiskStateUnavailable(Exception):
    """The DB couldn't be reached/queried - distinct from "no row exists
    yet" (genuine first run). Same convention as
    MT5Trader.get_open_positions() returning None (query failed) vs []
    (genuinely empty): collapsing "DB down" into "no data" here would let
    a DB hiccup at the exact restart moment silently reproduce the bug
    this module exists to close.
    """


def create_table() -> None:
    conn = db.get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS risk_state (
                id INT PRIMARY KEY DEFAULT 1,
                peak_equity DOUBLE PRECISION NOT NULL,
                daily_pnl DOUBLE PRECISION NOT NULL,
                daily_pnl_date DATE NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CHECK (id = 1)
            )
        """)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db.put_conn(conn)


def load() -> Optional[RiskState]:
    """Returns None only when no row exists yet (genuine first run).
    Raises RiskStateUnavailable if the DB itself couldn't be reached or
    queried - callers must not treat that the same as "no row"."""
    try:
        conn = db.get_conn()
    except Exception as e:
        raise RiskStateUnavailable(str(e)) from e
    try:
        cur = conn.cursor()
        cur.execute("SELECT peak_equity, daily_pnl, daily_pnl_date FROM risk_state WHERE id = 1")
        row = cur.fetchone()
        if row is None:
            return None
        return {"peak_equity": row[0], "daily_pnl": row[1], "daily_pnl_date": row[2]}
    except Exception as e:
        raise RiskStateUnavailable(str(e)) from e
    finally:
        db.put_conn(conn)


def save(peak_equity: float, daily_pnl: float, daily_pnl_date: date) -> bool:
    """Upsert the single state row. Catches and logs DB errors, returning
    False rather than raising - a save failure must never crash a live
    trade-close flow."""
    import logging
    logger = logging.getLogger(__name__)

    try:
        conn = db.get_conn()
    except Exception as e:
        logger.error(f"risk_state save failed (could not get DB connection): {e}")
        return False
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO risk_state (id, peak_equity, daily_pnl, daily_pnl_date, updated_at)
            VALUES (1, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (id) DO UPDATE SET
                peak_equity = EXCLUDED.peak_equity,
                daily_pnl = EXCLUDED.daily_pnl,
                daily_pnl_date = EXCLUDED.daily_pnl_date,
                updated_at = CURRENT_TIMESTAMP
        """, (peak_equity, daily_pnl, daily_pnl_date))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"risk_state save failed: {e}")
        return False
    finally:
        db.put_conn(conn)
