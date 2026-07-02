"""
Bootstrap historical OHLCV data into PostgreSQL.
Tries MT5 first; falls back to yfinance if MT5 IPC is unavailable.
Run: python scripts/bootstrap_data.py
"""
import sys
import os
import logging
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("bootstrap")

DB = {
    "dbname":   os.getenv("FOREX_DB_NAME", "forex_trading_db"),
    "user":     os.getenv("FOREX_DB_USER", "admin"),
    "password": os.getenv("FOREX_DB_PASSWORD", "admin"),
    "host":     os.getenv("FOREX_DB_HOST", "localhost"),
    "port":     int(os.getenv("FOREX_DB_PORT", 5432)),
}

# Symbol map: config name -> yfinance ticker
SYMBOLS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",
    "NZDUSD": "NZDUSD=X",
    "USDCHF": "USDCHF=X",
    "EURGBP": "EURGBP=X",
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
}

UPSERT_SQL = """
    INSERT INTO ohlcv_{table}
        (symbol, timeframe, timestamp, open, high, low, close, volume)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (symbol, timeframe, timestamp)
    DO UPDATE SET
        high   = GREATEST(ohlcv_{table}.high,  EXCLUDED.high),
        low    = LEAST   (ohlcv_{table}.low,   EXCLUDED.low),
        close  = EXCLUDED.close,
        volume = EXCLUDED.volume
"""


def fetch_mt5(symbol: str, days: int = 730):
    """Fetch OHLCV via MT5 Python API. Returns dict {timeframe: DataFrame} or None."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return None

    result = mt5.initialize(
        path="C:\\Program Files\\MetaTrader 5\\terminal64.exe",
        login=int(os.getenv("MT5_ACCOUNT", 0)),
        password=os.getenv("MT5_PASSWORD", ""),
        server=os.getenv("MT5_SERVER", ""),
        timeout=60000,
    )
    if not result:
        logger.warning("MT5 initialize failed: %s", mt5.last_error())
        mt5.shutdown()
        return None

    tf_map = {240: mt5.TIMEFRAME_H4, 1440: mt5.TIMEFRAME_D1}
    out = {}
    utc_from = datetime.utcnow() - timedelta(days=days)

    for tf_min, mt5_tf in tf_map.items():
        rates = mt5.copy_rates_from(symbol, mt5_tf, utc_from, 99999)
        if rates is None or len(rates) == 0:
            logger.warning("No MT5 data for %s tf=%s", symbol, tf_min)
            continue
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df.rename(columns={"time": "timestamp", "tick_volume": "volume"})
        df = df[["timestamp", "open", "high", "low", "close", "volume"]]
        out[tf_min] = df
        logger.info("MT5 fetched %s tf=%s: %d bars", symbol, tf_min, len(df))

    mt5.shutdown()
    return out if out else None


def fetch_yfinance(symbol: str, ticker: str, days: int = 730):
    """Fetch OHLCV via yfinance. Returns dict {timeframe: DataFrame}."""
    import yfinance as yf

    end = datetime.utcnow()
    start = end - timedelta(days=days)
    out = {}

    # yfinance intervals: 1h (max 730 days), 1d (unlimited)
    # 4H is built by resampling 1h data
    try:
        # --- 4H (resample from 1h) ---
        start_1h = end - timedelta(days=720)  # stay within 730-day limit
        df1h = yf.download(ticker, start=start_1h, end=end,
                           interval="1h", progress=False, auto_adjust=True)
        if not df1h.empty:
            df1h = df1h.reset_index()
            df1h.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df1h.columns]
            ts_col = "datetime" if "datetime" in df1h.columns else "date"
            df1h = df1h.rename(columns={ts_col: "timestamp"})
            df1h["timestamp"] = pd.to_datetime(df1h["timestamp"]).dt.tz_localize(None)
            df1h = df1h[["timestamp", "open", "high", "low", "close", "volume"]].dropna()
            df1h = df1h.set_index("timestamp")
            df4h = df1h.resample("4h").agg({
                "open":  "first",
                "high":  "max",
                "low":   "min",
                "close": "last",
                "volume":"sum",
            }).dropna().reset_index()
            out[240] = df4h
            logger.info("yfinance fetched %s tf=240 (resampled): %d bars", symbol, len(df4h))
        else:
            logger.warning("yfinance returned empty 1h data for %s", ticker)
    except Exception as e:
        logger.error("yfinance 1h error %s: %s", ticker, e)

    # --- Daily ---
    try:
        start_1d = end - timedelta(days=days)
        df1d = yf.download(ticker, start=start_1d, end=end,
                           interval="1d", progress=False, auto_adjust=True)
        if not df1d.empty:
            df1d = df1d.reset_index()
            df1d.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df1d.columns]
            ts_col = "datetime" if "datetime" in df1d.columns else "date"
            df1d = df1d.rename(columns={ts_col: "timestamp"})
            df1d["timestamp"] = pd.to_datetime(df1d["timestamp"]).dt.tz_localize(None)
            df1d = df1d[["timestamp", "open", "high", "low", "close", "volume"]].dropna()
            out[1440] = df1d
            logger.info("yfinance fetched %s tf=1440: %d bars", symbol, len(df1d))
        else:
            logger.warning("yfinance returned empty daily data for %s", ticker)
    except Exception as e:
        logger.error("yfinance daily error %s: %s", ticker, e)

    return out


def store(conn, symbol: str, data: dict):
    """Write {timeframe: DataFrame} into PostgreSQL."""
    table = symbol.lower()
    cur = conn.cursor()
    total = 0
    for tf_min, df in data.items():
        records = [
            (symbol, tf_min,
             row.timestamp.to_pydatetime() if hasattr(row.timestamp, "to_pydatetime") else row.timestamp,
             float(row.open), float(row.high), float(row.low), float(row.close), int(row.volume))
            for row in df.itertuples(index=False)
        ]
        execute_batch(cur, UPSERT_SQL.format(table=table), records, page_size=500)
        total += len(records)
        logger.info("Stored %d rows for %s tf=%s", len(records), symbol, tf_min)
    conn.commit()
    cur.close()
    return total


def main():
    logger.info("Connecting to PostgreSQL...")
    conn = psycopg2.connect(**DB)

    # Check if yfinance is available
    try:
        import yfinance
        yf_available = True
    except ImportError:
        yf_available = False
        logger.warning("yfinance not installed. Run: pip install yfinance")

    total_rows = 0
    for symbol, yf_ticker in SYMBOLS.items():
        logger.info("=== %s ===", symbol)

        # Try MT5 first (skip if MT5 IPC is unavailable to avoid 60s timeout per symbol)
        data = None  # set to fetch_mt5(symbol) to re-enable MT5

        # Fall back to yfinance
        if not data:
            if yf_available:
                logger.info("Falling back to yfinance for %s", symbol)
                data = fetch_yfinance(symbol, yf_ticker)
            else:
                logger.error("No data source available for %s — skip", symbol)
                continue

        if data:
            total_rows += store(conn, symbol, data)
        else:
            logger.error("No data obtained for %s", symbol)

    conn.close()
    logger.info("Bootstrap complete. Total rows inserted: %d", total_rows)


if __name__ == "__main__":
    main()
