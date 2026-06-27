"""
Historical backtest — replays all 4H candles through the live pipeline:
  features → XGBoost signal → daily SMA(50) trend filter → ATR-based SL/TP

Outputs a per-trade table and a summary per symbol.
Run:  python src/backtest_historical.py
"""
import sys
import logging
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from config.config import (
    POSTGRESQL, ACTIVE_SYMBOLS, SYMBOLS,
    SL_ATR_MULT, TP_ATR_MULT, ENTRY_SLIP_PIPS,
    ENSEMBLE_CONFIDENCE_THRESHOLD,
)
from src.features import FeatureEngine
from src.models.xgboost_classifier import XGBoostSignal

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger('Backtest')

LOOKBACK       = 60    # bars needed before first signal
DAILY_SMA_BARS = 50
DAILY_HIST     = 80    # daily bars to fetch — must be > DAILY_SMA_BARS + 11


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def fetch_4h(conn, symbol: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(
        f"SELECT timestamp, open, high, low, close, volume "
        f"FROM ohlcv_{symbol.lower()} WHERE timeframe=240 ORDER BY timestamp"
    )
    rows = cur.fetchall()
    cur.close()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=['timestamp','open','high','low','close','volume'])


def fetch_daily(conn, symbol: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(
        f"SELECT timestamp, close FROM ohlcv_{symbol.lower()} "
        f"WHERE timeframe=1440 ORDER BY timestamp"
    )
    rows = cur.fetchall()
    cur.close()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=['timestamp','close'])


# ---------------------------------------------------------------------------
# Trend filter (same logic as main.py _is_trend_aligned)
# ---------------------------------------------------------------------------
def trend_allows(daily_df: pd.DataFrame, bar_ts, signal: int) -> bool:
    sub = daily_df[daily_df['timestamp'] <= bar_ts].tail(DAILY_HIST)
    if len(sub) < DAILY_SMA_BARS + 11:
        return False
    closes = sub['close']
    sma50  = closes.rolling(DAILY_SMA_BARS).mean()
    cur_sma  = sma50.iloc[-1]
    prev_sma = sma50.iloc[-11]
    cur_cls  = closes.iloc[-1]
    if pd.isna(cur_sma) or pd.isna(prev_sma):
        return False
    uptrend   = (cur_cls > cur_sma) and (cur_sma > prev_sma)
    downtrend = (cur_cls < cur_sma) and (cur_sma < prev_sma)
    if signal ==  1 and not uptrend:   return False
    if signal == -1 and not downtrend: return False
    return True


# ---------------------------------------------------------------------------
# Simulate outcome: scan future bars for SL or TP hit
# ---------------------------------------------------------------------------
def simulate_outcome(df: pd.DataFrame, entry_idx: int,
                     direction: int, entry: float,
                     sl: float, tp: float):
    for i in range(entry_idx + 1, len(df)):
        hi = df['high'].iloc[i]
        lo = df['low'].iloc[i]
        if direction == 1:   # BUY
            if lo <= sl: return 'SL', df['timestamp'].iloc[i], sl - entry
            if hi >= tp: return 'TP', df['timestamp'].iloc[i], tp - entry
        else:                # SELL
            if hi >= sl: return 'SL', df['timestamp'].iloc[i], entry - sl
            if lo <= tp: return 'TP', df['timestamp'].iloc[i], entry - tp
    return 'OPEN', df['timestamp'].iloc[-1], df['close'].iloc[-1] - entry


# ---------------------------------------------------------------------------
# Main backtest loop
# ---------------------------------------------------------------------------
def run(conn, threshold: float = ENSEMBLE_CONFIDENCE_THRESHOLD):
    import psycopg2
    models_dir = str(Path(__file__).parent.parent / 'models')
    xgb = XGBoostSignal()
    xgb.load(models_dir)

    all_trades = []

    for symbol in ACTIVE_SYMBOLS:
        pip = SYMBOLS[symbol].get('pip_value', 0.0001)
        df  = fetch_4h(conn, symbol)
        daily_df = fetch_daily(conn, symbol)

        if len(df) < LOOKBACK + 10 or daily_df.empty:
            print(f'{symbol}: not enough data, skipping')
            continue

        # Build features for every bar
        engine = FeatureEngine(df)
        engine.add_technical_indicators() \
              .add_price_action_features() \
              .add_market_microstructure() \
              .normalize()
        feat = engine.features_normalized.values
        atr_series = engine.features['atr_14'].values

        last_trade_bar = -9999  # cooldown: skip within 1 candle of last trade

        for i in range(LOOKBACK, len(df)):
            if i - last_trade_bar < 1:
                continue

            window = feat[i - LOOKBACK:i]
            xgb_feat = window[-1].reshape(1, -1)
            xgb_pred  = int(xgb.predict_signal(xgb_feat)[0])
            if xgb_pred == 0:
                continue

            probas   = xgb.predict_proba(xgb_feat)[0]
            xgb_conf = probas[2] if xgb_pred == 1 else probas[0]
            if xgb_conf < threshold:
                continue

            bar_ts = df['timestamp'].iloc[i]
            if not trend_allows(daily_df, bar_ts, xgb_pred):
                continue

            # ATR check
            atr = atr_series[i]
            min_atr = pip * 10
            if np.isnan(atr) or atr < min_atr:
                continue

            current_price = df['close'].iloc[i]
            slip = ENTRY_SLIP_PIPS

            if xgb_pred == 1:
                entry = current_price + slip
                sl    = entry - SL_ATR_MULT * atr
                tp    = entry + TP_ATR_MULT * atr
            else:
                entry = current_price - slip
                sl    = entry + SL_ATR_MULT * atr
                tp    = entry - TP_ATR_MULT * atr

            outcome, close_ts, raw_pnl = simulate_outcome(df, i, xgb_pred, entry, sl, tp)

            sl_pips  = abs(entry - sl) / pip
            tp_pips  = abs(entry - tp) / pip
            pnl_pips = raw_pnl / pip

            all_trades.append({
                'symbol':    symbol,
                'open_time': bar_ts,
                'close_time': close_ts,
                'direction': 'BUY' if xgb_pred == 1 else 'SELL',
                'confidence': round(xgb_conf, 4),
                'entry':     round(entry, 5),
                'sl':        round(sl, 5),
                'tp':        round(tp, 5),
                'sl_pips':   round(sl_pips, 1),
                'tp_pips':   round(tp_pips, 1),
                'outcome':   outcome,
                'pnl_pips':  round(pnl_pips, 1),
            })
            last_trade_bar = i

        print(f'{symbol}: scanned {len(df)} bars')

    return pd.DataFrame(all_trades)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _symbol_summary(grp: pd.DataFrame) -> dict:
    closed = grp[grp['outcome'] != 'OPEN']
    wins   = closed[closed['outcome'] == 'TP']
    losses = closed[closed['outcome'] == 'SL']
    wr     = len(wins) / len(closed) * 100 if len(closed) else 0
    return {
        'Symbol':        grp['symbol'].iloc[0],
        'Trades':        len(grp),
        'TP':            len(wins),
        'SL':            len(losses),
        'Open':          len(grp[grp['outcome'] == 'OPEN']),
        'WinRate%':      round(wr, 1),
        'AvgPnL(pips)':  round(closed['pnl_pips'].mean(), 1) if len(closed) else 0.0,
        'TotalPnL(pips)':round(closed['pnl_pips'].sum(),  1),
    }


def _print_section(title: str, trades: pd.DataFrame, show_trades: bool = True):
    closed = trades[trades['outcome'] != 'OPEN']
    wins   = closed[closed['outcome'] == 'TP']

    print(f'\n{"="*80}')
    print(f'  {title}  —  {len(trades)} trades, {len(closed)} closed, {len(trades)-len(closed)} open')
    print(f'{"="*80}')

    if show_trades:
        cols = ['symbol','open_time','direction','confidence','sl_pips','tp_pips','outcome','pnl_pips']
        print(trades[cols].to_string(index=False))
        print()

    # Symbol summary
    summary = [_symbol_summary(g) for _, g in trades.groupby('symbol')]
    print(pd.DataFrame(summary).to_string(index=False))

    if not closed.empty:
        print(f'\n{"-"*80}')
        print(f'Win rate   : {len(wins)/len(closed)*100:.1f}%')
        print(f'Total P&L  : {closed["pnl_pips"].sum():.1f} pips')
        print(f'Avg/trade  : {closed["pnl_pips"].mean():.1f} pips')
        print(f'Best trade : {closed["pnl_pips"].max():.1f} pips')
        print(f'Worst trade: {closed["pnl_pips"].min():.1f} pips')
    print(f'{"="*80}')


def print_report(trades: pd.DataFrame):
    if trades.empty:
        print('\nNo trades triggered across all history. '
              'Consider lowering ENSEMBLE_CONFIDENCE_THRESHOLD.')
        return

    cutoff_2m = pd.Timestamp(datetime.utcnow()) - pd.DateOffset(months=2)
    recent    = trades[pd.to_datetime(trades['open_time']) >= cutoff_2m]

    # --- Last 2 months (full trade list) ---
    if recent.empty:
        print('\nNo trades in the last 2 months.')
    else:
        _print_section('LAST 2 MONTHS', recent, show_trades=True)

    # --- All-time (summary only, last 30 trades) ---
    print(f'\n{"="*80}')
    print(f'  ALL-TIME SUMMARY  —  {len(trades)} trades across {trades["symbol"].nunique()} symbols')
    print(f'{"="*80}')
    print('\nMost recent 30 trades:')
    cols = ['symbol','open_time','direction','confidence','sl_pips','tp_pips','outcome','pnl_pips']
    print(trades[cols].tail(30).to_string(index=False))
    print()
    summary = [_symbol_summary(g) for _, g in trades.groupby('symbol')]
    print(pd.DataFrame(summary).to_string(index=False))
    closed_all = trades[trades['outcome'] != 'OPEN']
    if not closed_all.empty:
        wins_all = closed_all[closed_all['outcome'] == 'TP']
        print(f'\n{"-"*80}')
        print(f'Win rate   : {len(wins_all)/len(closed_all)*100:.1f}%')
        print(f'Total P&L  : {closed_all["pnl_pips"].sum():.1f} pips')
        print(f'Avg/trade  : {closed_all["pnl_pips"].mean():.1f} pips')
        print(f'Best trade : {closed_all["pnl_pips"].max():.1f} pips')
        print(f'Worst trade: {closed_all["pnl_pips"].min():.1f} pips')
    print(f'{"="*80}\n')


def run_diagnostic(conn):
    """Show signal counts at each confidence threshold before and after trend filter."""
    models_dir = str(Path(__file__).parent.parent / 'models')
    xgb = XGBoostSignal()
    xgb.load(models_dir)

    thresholds = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
    # counts[t] = [directional_signals, trend_aligned]
    counts = {t: [0, 0] for t in thresholds}

    for symbol in ACTIVE_SYMBOLS:
        df       = fetch_4h(conn, symbol)
        daily_df = fetch_daily(conn, symbol)
        if len(df) < LOOKBACK + 10 or daily_df.empty:
            continue

        engine = FeatureEngine(df)
        engine.add_technical_indicators() \
              .add_price_action_features() \
              .add_market_microstructure() \
              .normalize()
        feat = engine.features_normalized.values

        for i in range(LOOKBACK, len(df)):
            xgb_feat = feat[i - LOOKBACK:i][-1].reshape(1, -1)
            xgb_pred = int(xgb.predict_signal(xgb_feat)[0])
            if xgb_pred == 0:
                continue
            probas   = xgb.predict_proba(xgb_feat)[0]
            xgb_conf = probas[2] if xgb_pred == 1 else probas[0]
            bar_ts   = df['timestamp'].iloc[i]
            aligned  = trend_allows(daily_df, bar_ts, xgb_pred)

            for t in thresholds:
                if xgb_conf >= t:
                    counts[t][0] += 1
                    if aligned:
                        counts[t][1] += 1

    print(f'\n{"="*55}')
    print('  SIGNAL DIAGNOSTIC  (all symbols combined)')
    print(f'{"="*55}')
    print(f'{"Threshold":>12}  {"XGB signals":>12}  {"Trend-aligned":>14}  {"% aligned":>10}')
    print(f'{"-"*55}')
    for t in thresholds:
        total, aligned = counts[t]
        pct = (aligned / total * 100) if total else 0
        marker = '  <-- current' if t == ENSEMBLE_CONFIDENCE_THRESHOLD else ''
        print(f'{t:>12.2f}  {total:>12,}  {aligned:>14,}  {pct:>9.1f}%{marker}')
    print(f'{"="*55}\n')


if __name__ == '__main__':
    import argparse, psycopg2

    parser = argparse.ArgumentParser()
    parser.add_argument('--threshold', type=float, default=ENSEMBLE_CONFIDENCE_THRESHOLD,
                        help='XGBoost confidence threshold (default: config value)')
    args = parser.parse_args()
    THRESHOLD = args.threshold

    print('Connecting to database...')
    conn = psycopg2.connect(**POSTGRESQL)

    print(f'Running signal diagnostic on {len(ACTIVE_SYMBOLS)} symbols...')
    run_diagnostic(conn)

    print(f'Running full backtest at threshold {THRESHOLD}...\n')
    trades = run(conn, threshold=THRESHOLD)
    conn.close()
    print_report(trades)

    out = Path(__file__).parent.parent / 'backtest_results.csv'
    if not trades.empty:
        trades.to_csv(out, index=False)
        print(f'Full results saved to {out}')
