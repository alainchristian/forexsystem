"""
Read-only analysis: track LSTM's directional prediction accuracy against
actual subsequent 4H candle closes pulled from Postgres.

LSTM is currently informational-only in ensemble.py — XGBoost alone gates
trades — precisely because this number has never been measured. This script
measures it without touching the live decision path.

Run: python scripts/analyze_lstm_directional_accuracy.py

Does not place, close, or modify any orders/positions.
"""
import re
import sys
import glob
import bisect
from pathlib import Path
from datetime import datetime
from collections import defaultdict

import psycopg2
from dotenv import load_dotenv

PROJECT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT))
load_dotenv(PROJECT / ".env")

from config.config import POSTGRESQL

# LSTM's logged signal was silently wrong (saturated to SELL at strength=1.0
# for every symbol, regardless of the real prediction) before commit 5f21594
# (2026-07-02) fixed a scaler mismatch. Predictions logged before that fix
# aren't evaluated here — they don't reflect the model's real behavior.
LSTM_FIX_CUTOFF = datetime(2026, 7, 2, 11, 45)

ENSEMBLE_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*"
    r"LSTM: (BUY|SELL|FLAT) \(strength=([\d.]+)\)\s+"
    r"XGB: (BUY|SELL|FLAT) \(conf=([\d.]+)\)"
)
DECISION_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*?"
    r"(\w+): Ensemble -> (?:BUY|SELL|NEUTRAL) \| confidence: [\d.]+%"
)

DIR_TO_SIGN = {"BUY": 1, "SELL": -1, "FLAT": 0}


def parse_predictions():
    """Walk every rotated log file oldest-to-newest, pairing each
    'LSTM: ... XGB: ...' line with the symbol name from the decision line
    main.py logs immediately after it.

    process_symbol() runs strictly sequentially — one symbol fully to
    completion before the next starts (main.py:221-222) — so the most
    recently seen LSTM/XGB line always belongs to the next decision line.
    """
    log_files = sorted(
        glob.glob(str(PROJECT / "logs" / "forex_system.log*")),
        key=lambda p: Path(p).stat().st_mtime,
    )

    predictions = []
    unpaired = 0
    pending = None
    for path in log_files:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = ENSEMBLE_LINE_RE.match(line)
                if m:
                    ts_str, lstm_dir, lstm_strength, xgb_dir, xgb_conf = m.groups()
                    pending = {
                        "ts": datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S"),
                        "lstm_dir": lstm_dir,
                        "lstm_strength": float(lstm_strength),
                        "xgb_dir": xgb_dir,
                        "xgb_conf": float(xgb_conf),
                    }
                    continue
                m = DECISION_LINE_RE.match(line)
                if m:
                    if pending is None:
                        unpaired += 1
                        continue
                    pending["symbol"] = m.group(2)
                    predictions.append(pending)
                    pending = None
    if unpaired:
        print(f"Warning: {unpaired} decision lines had no preceding LSTM/XGB line (skipped).")
    return predictions


def load_candles(conn, symbol: str):
    """All 4H candles for symbol, oldest to newest: list of (timestamp, close)."""
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT timestamp, close FROM ohlcv_{symbol.lower()} "
            f"WHERE timeframe = 240 ORDER BY timestamp"
        )
        return [(ts, float(close)) for ts, close in cur.fetchall()]
    except psycopg2.Error:
        return []


def resolve_predictions(directional, candles_by_symbol):
    """Map each prediction to the 4H candle boundary in effect when it was
    made, then keep only the freshest (latest-timestamped) prediction per
    boundary as that boundary's representative.

    This grouping has to happen against the real candle timeline rather than
    by deduping identical (lstm_dir, lstm_strength) values first: LSTM's
    continuous output drifts slightly cycle-to-cycle even within one still-
    open candle (most likely from the in-progress candle's OHLC being
    updated as it forms) — confirmed empirically running this script, where
    deduping by exact value only collapsed ~85k raw log rows to ~52k
    'distinct' predictions, nowhere near the handful of real candle closes
    that occurred in that window. Grouping by actual candle boundary instead
    gives one row per real, independent look.
    """
    by_boundary = {}
    unresolved = 0
    for p in directional:
        candles = candles_by_symbol.get(p["symbol"], [])
        idx = bisect.bisect_right(candles, p["ts"], key=lambda c: c[0]) - 1
        if idx < 0 or idx + 1 >= len(candles):
            unresolved += 1
            continue
        boundary_ts = candles[idx][0]
        key = (p["symbol"], boundary_ts)
        existing = by_boundary.get(key)
        if existing is None or p["ts"] > existing["ts"]:
            by_boundary[key] = {
                **p,
                "baseline_close": candles[idx][1],
                "next_close": candles[idx + 1][1],
            }
    return list(by_boundary.values()), unresolved


def main():
    print("Parsing logs for LSTM predictions...")
    predictions = [p for p in parse_predictions() if p["ts"] >= LSTM_FIX_CUTOFF]
    print(f"Found {len(predictions)} LSTM prediction log entries since the {LSTM_FIX_CUTOFF} scaler fix.\n")

    directional = [p for p in predictions if p["lstm_dir"] != "FLAT"]
    print(f"{len(directional)} of those are directional (LSTM predicted BUY or SELL).\n")

    symbols = sorted({p["symbol"] for p in directional})
    conn = psycopg2.connect(**POSTGRESQL)
    conn.autocommit = True
    try:
        candles_by_symbol = {s: load_candles(conn, s) for s in symbols}
    finally:
        conn.close()

    resolved, unresolved = resolve_predictions(directional, candles_by_symbol)
    results = []
    for r in resolved:
        actual_sign = 1 if r["next_close"] > r["baseline_close"] else -1
        results.append({
            **r,
            "actual_sign": actual_sign,
            "lstm_hit": actual_sign == DIR_TO_SIGN[r["lstm_dir"]],
            "xgb_directional": r["xgb_dir"] != "FLAT",
            "xgb_hit": r["xgb_dir"] != "FLAT" and actual_sign == DIR_TO_SIGN[r["xgb_dir"]],
        })

    print(f"{unresolved} predictions skipped — next candle hasn't closed in the DB yet.")
    print(f"Resolved {len(results)} distinct candle-boundary predictions against actual outcomes.\n")

    if not results:
        print("Nothing to report yet — check back once more 4H candles have closed.")
        return

    lstm_hits = sum(r["lstm_hit"] for r in results)
    print("--- LSTM directional accuracy (all symbols) ---")
    print(f"n={len(results)}  hit_rate={lstm_hits/len(results):.1%}  (50% = coin flip)\n")

    xgb_dir_results = [r for r in results if r["xgb_directional"]]
    if xgb_dir_results:
        xgb_hits = sum(r["xgb_hit"] for r in xgb_dir_results)
        print("--- XGBoost directional accuracy, same resolved set, XGB-directional rows only ---")
        print(f"n={len(xgb_dir_results)}  hit_rate={xgb_hits/len(xgb_dir_results):.1%}\n")

    agree = [r for r in xgb_dir_results if DIR_TO_SIGN[r["lstm_dir"]] == DIR_TO_SIGN[r["xgb_dir"]]]
    if agree:
        agree_hits = sum(r["lstm_hit"] for r in agree)
        print("--- When LSTM and XGB agree on direction ---")
        print(f"n={len(agree)}  hit_rate={agree_hits/len(agree):.1%}\n")

    print("--- LSTM accuracy bucketed by strength ---")
    buckets = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
    for lo, hi in buckets:
        bucket = [r for r in results if lo <= r["lstm_strength"] < hi]
        if not bucket:
            print(f"strength {lo:.1f}-{hi:.1f}: no predictions")
            continue
        hits = sum(r["lstm_hit"] for r in bucket)
        print(f"strength {lo:.1f}-{hi:.1f}: n={len(bucket)} hit_rate={hits/len(bucket):.1%}")

    print("\n--- LSTM accuracy per symbol ---")
    by_symbol = defaultdict(list)
    for r in results:
        by_symbol[r["symbol"]].append(r)
    for symbol in sorted(by_symbol):
        rows = by_symbol[symbol]
        hits = sum(r["lstm_hit"] for r in rows)
        print(f"{symbol:<10} n={len(rows):<5} hit_rate={hits/len(rows):.1%}")

    print(
        "\nNote: each 4H candle only closes 6x/day per symbol, so this sample "
        "grows slowly and a handful of predictions can swing a bucket by "
        "10-20 points. Re-run periodically and track the trend rather than "
        "acting on any single run — see the lstm-ensemble-gating memory for "
        "what evidence would justify wiring LSTM into the live decision."
    )


if __name__ == "__main__":
    main()
