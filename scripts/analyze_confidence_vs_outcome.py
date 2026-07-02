"""
Read-only analysis: correlate logged entry confidence with actual trade
outcomes from MT5's own deal history (the broker's authoritative record,
unaffected by the P&L-logging race condition that was fixed in mt5_trader.py).

Run: python scripts/analyze_confidence_vs_outcome.py

Does not place, close, or modify any orders/positions.
"""
import os
import re
import glob
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

from dotenv import load_dotenv

PROJECT = Path(__file__).parent.parent
load_dotenv(PROJECT / ".env")

import MetaTrader5 as mt5

MAGIC = 123456

def load_mt5_deals():
    ok = mt5.initialize(
        login=int(os.getenv("MT5_ACCOUNT")),
        password=os.getenv("MT5_PASSWORD"),
        server=os.getenv("MT5_SERVER"),
        timeout=15000,
    )
    if not ok:
        raise RuntimeError(f"mt5.initialize failed: {mt5.last_error()}")

    since = datetime.utcnow() - timedelta(days=7)
    deals = mt5.history_deals_get(since, datetime.utcnow())
    mt5.shutdown()
    if deals is None:
        return {}

    # Sum profit per position_id (entry deal has 0 profit, exit deal(s) carry it)
    by_position = defaultdict(lambda: {"profit": 0.0, "symbol": None, "close_time": None, "entry_time": None})
    for d in deals:
        if d.magic != MAGIC:
            continue
        pos = by_position[d.position_id]
        pos["profit"] += d.profit
        pos["symbol"] = d.symbol
        t = datetime.utcfromtimestamp(d.time)
        if d.entry == 0:  # DEAL_ENTRY_IN
            pos["entry_time"] = t
        else:
            pos["close_time"] = t
    return by_position


def load_logged_confidence():
    """Parse every log file for Order-opened lines and pair each with the
    most recent preceding Signal/Confidence line for the same symbol."""
    log_files = sorted(
        glob.glob(str(PROJECT / "logs" / "forex_system.log*")),
        key=lambda p: os.path.getmtime(p),
    )

    open_re = re.compile(r"Order #(\d+) opened: (\w+) (BUY|SELL)")
    signal_re = re.compile(r"(\w+) Signal: (BUY|SELL) \| Confidence: ([\d.]+)%")

    order_confidence = {}
    for path in log_files:
        last_signal = {}  # symbol -> confidence
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                sm = signal_re.search(line)
                if sm:
                    last_signal[sm.group(1)] = float(sm.group(3))
                    continue
                om = open_re.search(line)
                if om:
                    order_id, symbol, direction = om.group(1), om.group(2), om.group(3)
                    if symbol in last_signal:
                        order_confidence[int(order_id)] = last_signal[symbol]
    return order_confidence


def main():
    print("Loading MT5 deal history (magic=123456, last 7 days)...")
    positions = load_mt5_deals()
    print(f"Found {len(positions)} closed/open positions with this bot's magic number.\n")

    print("Parsing logs for entry confidence...")
    confidences = load_logged_confidence()
    print(f"Found {len(confidences)} order-open events with a matched confidence value.\n")

    matched = []
    for ticket, pos in positions.items():
        if ticket in confidences and pos["close_time"] is not None:
            matched.append({
                "ticket": ticket,
                "symbol": pos["symbol"],
                "confidence": confidences[ticket],
                "profit": pos["profit"],
                "entry_time": pos["entry_time"],
                "close_time": pos["close_time"],
            })

    print(f"Matched {len(matched)} closed positions to a logged entry confidence.\n")
    print(f"{'Ticket':<12}{'Symbol':<9}{'Conf%':<8}{'P&L':<10}{'Entry':<20}{'Close'}")
    for m in sorted(matched, key=lambda x: x["entry_time"] or datetime.min):
        et = m["entry_time"].strftime("%Y-%m-%d %H:%M") if m["entry_time"] else "?"
        ct = m["close_time"].strftime("%Y-%m-%d %H:%M") if m["close_time"] else "?"
        print(f"{m['ticket']:<12}{m['symbol']:<9}{m['confidence']:<8.2f}{m['profit']:<10.2f}{et:<20}{ct}")

    if not matched:
        print("\nNo positions could be matched to a logged confidence value.")
        return

    print("\n--- Bucketed by confidence ---")
    buckets = [(0.55, 0.58), (0.58, 0.65), (0.65, 0.75), (0.75, 1.01)]
    for lo, hi in buckets:
        bucket = [m for m in matched if lo <= m["confidence"] / 100 < hi]
        if not bucket:
            print(f"{lo:.0%}-{hi:.0%}: no trades")
            continue
        wins = [m for m in bucket if m["profit"] > 0]
        total_pnl = sum(m["profit"] for m in bucket)
        print(
            f"{lo:.0%}-{hi:.0%}: n={len(bucket)} win_rate={len(wins)/len(bucket):.0%} "
            f"total_pnl=${total_pnl:+.2f} avg_pnl=${total_pnl/len(bucket):+.2f}"
        )
    print(
        "\nNote: with this few trades, bucket win rates are not statistically "
        "meaningful (a single trade can swing a bucket by 33-100 points). "
        "Treat this as a progress check, not a basis for changing "
        "ENSEMBLE_CONFIDENCE_THRESHOLD, until sample sizes are much larger."
    )


if __name__ == "__main__":
    main()
