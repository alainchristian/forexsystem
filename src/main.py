import asyncio
import logging
import logging.config
import sys
import os
import time as time_module
from datetime import datetime, time
from typing import Dict
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).parent.parent))

# Windows cp1252 console can't encode emoji — switch stdout/stderr to UTF-8 first
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import config.config as cfg
from src.risk_manager import RiskManager, RiskConfig
from src.telegram_bot import TelegramNotifier
from src.mt5_trader import MT5Trader
from src import db
from src import risk_state

from src.models.lstm_predictor import LSTMPredictor
from src.models.xgboost_classifier import XGBoostSignal
from src.models.ensemble import EnsembleStrategy
from src.features import load_scaler

def _configure_logging() -> None:
    """Attach the file/console handlers from config.config.LOGGING to the
    root logger. Deferred to the real entry point (guarded by
    `if __name__ == '__main__'` below) rather than run as an import-time
    side effect - importing this module just for its classes (e.g. from a
    test suite building a TradingSystem/MT5Trader) used to redirect real
    log output into the live logs/forex_system.log file, since MT5Trader's
    own logger propagates to the root logger this configures."""
    logging.config.dictConfig(cfg.LOGGING)

    # Suppress noisy third-party debug output that contains emoji and
    # causes UnicodeEncodeError on Windows cp1252 consoles
    logging.getLogger('telegram').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)


logger = logging.getLogger('Main')


class TradingSystem:
    def __init__(self, config: Dict):
        self.config = config

        try:
            risk_state.create_table()
        except Exception as e:
            logger.error(f"Could not create risk_state table: {e} — peak_equity/daily_pnl "
                         f"persistence across restarts will be unavailable until this is fixed")

        self.risk_mgr = RiskManager(RiskConfig(
            account_equity=config['initial_capital'],
            risk_per_trade=config['risk_per_trade'],
            max_daily_loss_pct=config['max_daily_loss'],
            max_open_trades=config['max_open_trades']
        ), on_state_changed=self._persist_risk_state)

        self.telegram = TelegramNotifier(
            bot_token=config['telegram_token'],
            chat_id=config['telegram_chat_id']
        )

        self.trader = MT5Trader(
            account=config['mt5_account'],
            password=config['mt5_password'],
            server=config['mt5_server'],
            risk_manager=self.risk_mgr,
            telegram_notifier=self.telegram
        )

        self.lstm = LSTMPredictor(lookback=60)

        model_dir = str(Path(__file__).parent.parent / 'models')
        try:
            self.lstm.load(model_dir)
            self.xgb = XGBoostSignal()
            self.xgb.load(model_dir)
            logger.info("Models loaded successfully")
        except Exception as e:
            logger.critical(
                f"Model load failed: {e} — train models first with train_models.py"
            )
            sys.exit(1)

        self.ensemble = EnsembleStrategy(
            self.lstm, self.xgb,
            threshold_confidence=cfg.ENSEMBLE_CONFIDENCE_THRESHOLD
        )

        # Per-symbol feature scalers persisted by train_models.py. Loaded once
        # here (not re-read from disk every cycle) so process_symbol() can
        # transform live features against training-time statistics instead of
        # fitting a fresh scaler on a handful of live bars each cycle — that
        # mismatch was a live train/serve skew affecting every prediction.
        self.feature_scalers: Dict[str, StandardScaler] = {}
        missing_scalers = []
        for symbol in cfg.ACTIVE_SYMBOLS:
            scaler_path = cfg.FEATURE_SCALER_DIR / f"{symbol}.pkl"
            if scaler_path.exists():
                self.feature_scalers[symbol] = load_scaler(str(scaler_path))
            else:
                missing_scalers.append(symbol)
        if missing_scalers:
            logger.critical(
                f"No persisted feature scaler for: {missing_scalers} — "
                f"these symbols will not trade until train_models.py is run."
            )
        if not self.feature_scalers:
            logger.critical(
                "Zero feature scalers loaded — run train_models.py before starting main.py"
            )
            sys.exit(1)

        # In-memory price cache for trailing stops — absorbs a single
        # transient get_current_price() failure without losing the last
        # known-good price.
        self._price_cache: Dict[str, float] = {}
        # Last time each symbol's price cache was successfully refreshed, and
        # the last time a stale-price alert was sent for it — used to detect
        # positions with no live price data (see update_trailing_stops).
        self._price_cache_updated_at: Dict[str, datetime] = {}
        self._price_cache_last_alert: Dict[str, datetime] = {}

    def _persist_risk_state(self, peak_equity: float, daily_pnl: float) -> None:
        """RiskManager's on_state_changed hook - persists so a restart doesn't
        silently reset the drawdown/daily-loss circuit breakers' memory (see
        src/risk_state.py). Logs rather than raises on failure - a persistence
        hiccup must never interrupt live trading."""
        if not risk_state.save(peak_equity, daily_pnl, datetime.utcnow().date()):
            logger.error("Failed to persist risk state (peak_equity/daily_pnl) — see risk_state.save log above")

    async def _sync_account_and_risk_state(self) -> None:
        """Sync RiskManager's account_equity/peak_equity/daily_pnl with live MT5
        equity and any persisted risk state at startup. Restoring peak_equity/
        daily_pnl from risk_state.load() (rather than always re-deriving
        peak_equity from live equity) is what keeps the drawdown/daily-loss
        circuit breakers' memory intact across a crash/restart."""
        acc_info = self.trader.get_account_info()
        if not (acc_info and acc_info.get('equity', 0) > 0):
            logger.info(f"Account equity unavailable — using initial_capital: ${self.config['initial_capital']:.2f}")
            return

        logger.info(f"Syncing Risk Manager with live MT5 equity: ${acc_info['equity']:.2f}")
        self.risk_mgr.config.account_equity = acc_info['equity']

        try:
            persisted = risk_state.load()
        except risk_state.RiskStateUnavailable as e:
            persisted = None
            logger.error(f"Risk state DB unavailable at startup: {e}")
            await self.telegram.send_alert(
                "⚠️ Risk state DB unavailable at startup — peak_equity/daily_pnl "
                "circuit breakers are starting from live equity only, not their "
                "persisted historical values."
            )

        if persisted:
            # All-time high-water mark: never let a restart's live equity reading
            # count as a new peak lower than one already recorded.
            self.risk_mgr.peak_equity = max(persisted['peak_equity'], acc_info['equity'])
            if persisted['daily_pnl_date'] == datetime.utcnow().date():
                self.risk_mgr.daily_pnl = persisted['daily_pnl']
            # else: date has rolled over since the last save - leave daily_pnl at
            # 0.0. This also self-heals a missed daily reset (reset_daily_stats()
            # only fires in a 1-minute UTC window; if the bot was down then, this
            # catches it on the next restart instead of carrying yesterday's P&L
            # forward indefinitely).
        else:
            self.risk_mgr.peak_equity = acc_info['equity']  # genuine first run

    async def run(self):
        """Main trading loop"""
        await self.telegram.initialize()
        await self.telegram.setup_controls(self.trader)

        if not self.trader.initialize():
            logger.error("Failed to initialize MT5")
            if not self.config.get('mock_mode', False):
                return
        else:
            await self._sync_account_and_risk_state()

            # Recover any positions that survived a previous crash
            await self._reconcile_positions()

        logger.info("Trading system initialized, entering main loop...")
        await self.telegram.send_alert("🚀 <b>Trading System Started</b>")

        self._last_data_refresh: float = 0.0

        while True:
            try:
                current_time = datetime.utcnow().time()

                # Refresh market data every 5 minutes before processing signals
                now = time_module.time()
                if now - self._last_data_refresh >= cfg.DATA_CONFIG['update_interval']:
                    await asyncio.to_thread(self._refresh_market_data)
                    self._last_data_refresh = now

                # Drop any positions from memory that MT5 has already closed (SL/TP hit)
                await self._sync_closed_positions()

                # Retry P&L confirmation for closes where history lookup failed earlier
                await self.trader.reconcile_pending_pnl()

                for symbol in self.config['symbols']:
                    await self.process_symbol(symbol)

                await self.update_trailing_stops()

                reset_hour = cfg.DAILY_RESET_HOUR_UTC
                if time(reset_hour, 55) <= current_time < time(reset_hour, 56):
                    await self.send_daily_report()
                    self.risk_mgr.reset_daily_stats()

                await asyncio.sleep(60)

            except Exception as e:
                logger.error(f"Main loop exception: {e}", exc_info=True)
                await self.telegram.send_alert(f"⚠️ Error: {str(e)[:100]}")
                await asyncio.sleep(60)

    async def _sync_closed_positions(self):
        """Remove positions from open_positions that MT5 has already closed (SL/TP hit)."""
        if not self.trader.mt5_initialized:
            return
        live_positions = self.trader.get_open_positions()
        if live_positions is None:
            # Query failed - do NOT treat every tracked position as closed
            # just because we couldn't confirm what's actually open this
            # cycle. get_open_positions() used to collapse "query failed"
            # and "genuinely zero positions" into the same [], which made
            # a single transient MT5 hiccup look like every open position
            # had hit its SL/TP simultaneously.
            logger.warning("Could not fetch live positions from MT5 this cycle - skipping sync")
            return
        live_tickets = {p.ticket for p in live_positions}
        stale = [tid for tid in list(self.trader.open_positions) if tid not in live_tickets]
        for tid in stale:
            pos = self.trader.open_positions.pop(tid)
            pnl = await self.trader.get_closed_pnl(tid)
            direction = "BUY" if pos['direction'] > 0 else "SELL"

            if pnl is None:
                logger.error(
                    f"Synced: position #{tid} ({pos['symbol']} {direction}) closed by MT5 "
                    f"but P&L could not be confirmed — daily P&L NOT updated, queuing for reconciliation"
                )
                self.trader.queue_pnl_reconciliation(tid, pos['symbol'], "SL/TP hit")
                await self.telegram.send_alert(
                    f"⚪ <b>{pos['symbol']}</b> {direction} closed (SL/TP hit)\n"
                    f"Entry: {pos['entry']:.5f}\n"
                    f"P&L: unknown — will retry confirmation | Daily P&L: ${self.risk_mgr.daily_pnl:+.2f}"
                )
                continue

            self.risk_mgr.update_daily_pnl(pnl)
            logger.info(
                f"Synced: position #{tid} ({pos['symbol']} {direction}) "
                f"closed by MT5 (SL/TP hit) | P&L: ${pnl:+.2f} "
                f"| Daily P&L: ${self.risk_mgr.daily_pnl:+.2f}"
            )
            emoji = "🟢" if pnl >= 0 else "🔴"
            await self.telegram.send_alert(
                f"{emoji} <b>{pos['symbol']}</b> {direction} closed (SL/TP hit)\n"
                f"Entry: {pos['entry']:.5f}\n"
                f"P&L: ${pnl:+.2f} | Daily P&L: ${self.risk_mgr.daily_pnl:+.2f}"
            )

    async def _reconcile_positions(self):
        """On startup, rebuild open_positions from whatever MT5 has open."""
        mt5_positions = self.trader.get_open_positions()
        if mt5_positions is None:
            logger.error("Could not fetch positions from MT5 at startup - open_positions will start empty")
            return
        recovered = 0
        for pos in mt5_positions:
            if pos.magic == 123456 and pos.ticket not in self.trader.open_positions:
                self.trader.open_positions[pos.ticket] = {
                    'symbol': pos.symbol,
                    'direction': 1 if pos.type == 0 else -1,
                    'volume': pos.volume,
                    'entry': pos.price_open,
                    'sl': pos.sl,
                    'tp': pos.tp,
                    # MT5's pos.time is broker-server time, not true UTC or
                    # local time, so fromtimestamp(pos.time) produced wildly
                    # wrong (sometimes future-dated) opened_at values -
                    # negative hold times in the Ranked Replacement guardrail
                    # and corrupted trade-duration numbers in daily reports.
                    # Use the reconciliation moment instead: it undercounts
                    # true hold time for a recovered position, but that's a
                    # small, honest inaccuracy instead of a wrong one, and it
                    # also means a fresh restart can't immediately start
                    # churning positions it just recovered.
                    'opened_at': datetime.now(),
                    'order_id': pos.ticket,
                    'confidence': cfg.ENSEMBLE_CONFIDENCE_THRESHOLD,
                }
                recovered += 1

        if recovered:
            logger.info(f"Reconciled {recovered} open position(s) from MT5")
            await self.telegram.send_alert(
                f"🔄 Recovered <b>{recovered}</b> open position(s) from previous session"
            )

    def _refresh_market_data(self):
        """Fetch the latest candles from MT5 and upsert into the DB.

        Runs every DATA_CONFIG['update_interval'] seconds so the DB never
        goes stale. Uses the already-initialized MT5 session from self.trader.
        """
        if not self.trader.mt5_initialized:
            return

        try:
            import MetaTrader5 as mt5
        except ImportError:
            return

        tf_map = {240: mt5.TIMEFRAME_H4, 1440: mt5.TIMEFRAME_D1}
        # Fetch enough bars to cover the ATR(14) window plus a few extras
        bars_per_tf = {240: 20, 1440: 10}

        upsert_sql = """
            INSERT INTO {table}
                (symbol, timeframe, timestamp, open, high, low, close, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, timeframe, timestamp)
            DO UPDATE SET
                high   = GREATEST({table}.high,   EXCLUDED.high),
                low    = LEAST   ({table}.low,    EXCLUDED.low),
                close  = EXCLUDED.close,
                volume = EXCLUDED.volume
        """

        conn = db.get_conn()
        try:
            cursor = conn.cursor()
            updated = 0
            for symbol in cfg.ACTIVE_SYMBOLS:
                table = f"ohlcv_{symbol.lower()}"
                for tf_min, mt5_tf in tf_map.items():
                    n_bars = bars_per_tf[tf_min]
                    rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, n_bars)
                    if rates is None or len(rates) == 0:
                        continue
                    records = [
                        (symbol, tf_min,
                         datetime.utcfromtimestamp(int(r['time'])),
                         float(r['open']), float(r['high']),
                         float(r['low']),  float(r['close']),
                         int(r['tick_volume']))
                        for r in rates
                    ]
                    from psycopg2.extras import execute_batch
                    execute_batch(cursor, upsert_sql.format(table=table), records)
                    updated += len(records)

            conn.commit()
            logger.debug(f"Market data refreshed: {updated} candle records upserted")
        except Exception as e:
            conn.rollback()
            logger.error(f"Market data refresh failed: {e}")
        finally:
            db.put_conn(conn)

    def _fetch_ohlcv(self, symbol: str) -> pd.DataFrame | None:
        """Fetch the 200 most recent 4H candles (timeframe=240)."""
        conn = db.get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT timestamp, open, high, low, close, volume
                FROM ohlcv_{symbol.lower()}
                WHERE timeframe = 240
                ORDER BY timestamp DESC LIMIT 200
            """)
            rows = cursor.fetchall()
            if not rows:
                return None
            return pd.DataFrame(
                rows[::-1],
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
            )
        finally:
            db.put_conn(conn)

    def _fetch_daily_closes(self, symbol: str) -> pd.DataFrame | None:
        """Fetch the 60 most recent Daily candles (timeframe=1440) for trend filtering."""
        conn = db.get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT timestamp, close
                FROM ohlcv_{symbol.lower()}
                WHERE timeframe = 1440
                ORDER BY timestamp DESC LIMIT 60
            """)
            rows = cursor.fetchall()
            if not rows or len(rows) < 52:
                return None
            return pd.DataFrame(rows[::-1], columns=['timestamp', 'close'])
        finally:
            db.put_conn(conn)

    @staticmethod
    def _is_trend_aligned(daily_df: pd.DataFrame, signal: int) -> bool:
        """
        Return True if signal direction agrees with the Daily SMA(50) trend.

        Uptrend:   last Daily close > SMA(50) AND SMA(50) is higher than it was 10 days ago.
        Downtrend: last Daily close < SMA(50) AND SMA(50) is lower than it was 10 days ago.
        BUY signals are only allowed in uptrends; SELL signals only in downtrends.
        If there isn't enough data to decide, the filter passes (does not block).
        """
        closes = daily_df['close']
        sma50 = closes.rolling(50).mean()

        current_sma = sma50.iloc[-1]
        prev_sma = sma50.iloc[-11]   # 10 sessions ago
        current_close = closes.iloc[-1]

        if pd.isna(current_sma) or pd.isna(prev_sma):
            return True  # insufficient history — don't block

        uptrend = (current_close > current_sma) and (current_sma > prev_sma)
        downtrend = (current_close < current_sma) and (current_sma < prev_sma)

        if signal == 1 and not uptrend:
            return False
        if signal == -1 and not downtrend:
            return False
        return True

    async def process_symbol(self, symbol: str):
        """Analyze and potentially trade a single symbol."""
        from src.features import FeatureEngine

        # Reject unknown symbols before they reach the DB query
        if symbol not in cfg.ALLOWED_SYMBOLS:
            logger.error(f"Unknown symbol rejected: {symbol}")
            return

        # Refuse to trade a symbol with no persisted feature scaler rather than
        # silently falling back to fitting one on live data — that fallback is
        # exactly the train/serve skew bug this gate exists to prevent.
        if symbol not in self.feature_scalers:
            logger.error(f"{symbol}: no persisted feature scaler — refusing to trade until retrained")
            return

        try:
            df = await asyncio.to_thread(self._fetch_ohlcv, symbol)
            if df is None or len(df) < 150:
                logger.warning(f"Insufficient data for {symbol}")
                return

            # Refuse to trade on stale data — if the newest 4H candle is older
            # than the allowed threshold the SL/TP will be calculated against a
            # stale price and the actual fill will land far from where we planned.
            # In mock_mode we relax this to 48h because yfinance forex hourly
            # data has a ~17h inherent delay; live mode keeps the strict 8h limit.
            latest_ts = pd.Timestamp(df['timestamp'].iloc[-1])
            data_age_hours = (datetime.utcnow() - latest_ts).total_seconds() / 3600
            max_data_age = 48 if self.config.get('mock_mode', False) else 8
            if data_age_hours > max_data_age:
                logger.warning(
                    f"{symbol}: Data too stale ({data_age_hours:.1f}h old, newest candle {latest_ts}) "
                    f"— skipping trade. Run data ingestion."
                )
                return

            fe = FeatureEngine(df)
            fe.add_technical_indicators() \
              .add_price_action_features() \
              .add_market_microstructure()
            fe.scaler = self.feature_scalers[symbol]
            fe.normalize(fit=False)

            recent_data = fe.features_normalized.iloc[-60:].values
            current_price = df['close'].iloc[-1]

            signal, confidence = self.ensemble.generate_signal(recent_data, current_price)

            dir_label = {1: "BUY", -1: "SELL", 0: "NEUTRAL"}[signal]
            logger.info(f"{symbol}: Ensemble -> {dir_label} | confidence: {confidence:.2%}")

            if signal == 0:
                return

            # ── Daily trend filter ────────────────────────────────────────────
            # Only trade WITH the Daily SMA(50) direction. This blocks the most
            # common failure mode: entering counter-trend on a 4H bounce.
            # If daily data is missing, block rather than bypass — unknown trend
            # is not a reason to trade.
            daily_df = await asyncio.to_thread(self._fetch_daily_closes, symbol)
            if daily_df is None:
                dir_str = "BUY" if signal > 0 else "SELL"
                logger.warning(
                    f"{symbol}: {dir_str} signal blocked — "
                    f"no Daily data available (run bootstrap to populate timeframe=1440)"
                )
                return
            if not self._is_trend_aligned(daily_df, signal):
                dir_str = "BUY" if signal > 0 else "SELL"
                logger.info(
                    f"{symbol}: {dir_str} signal skipped — "
                    f"counter to Daily SMA(50) trend (close={daily_df['close'].iloc[-1]:.5f})"
                )
                return
            # ─────────────────────────────────────────────────────────────────

            # ATR-based SL/TP — multipliers are configurable in config.py
            atr = fe.features['atr_14'].iloc[-1]

            # Sanity-check ATR: if it's NaN or unrealistically small the SL will
            # land right at entry and get hit immediately.  Enforce a per-symbol
            # floor based on pip_value (minimum 10 pips worth of ATR).
            pip_value = cfg.SYMBOLS[symbol].get('pip_value', 0.0001)
            min_atr = pip_value * 10  # 10 pips minimum
            if pd.isna(atr) or atr < min_atr:
                logger.warning(
                    f"{symbol}: ATR invalid ({atr:.6f}) — expected ≥ {min_atr:.5f} "
                    f"({min_atr/pip_value:.0f} pips). Skipping trade. Check 4H data quality in DB."
                )
                return

            # Apply entry slippage to align backtest P&L with live conditions
            slip = cfg.ENTRY_SLIP_PIPS
            if signal == 1:  # BUY
                entry = current_price + slip
                stop_loss = entry - (cfg.SL_ATR_MULT * atr)
                take_profit = entry + (cfg.TP_ATR_MULT * atr)
            else:  # SELL
                entry = current_price - slip
                stop_loss = entry + (cfg.SL_ATR_MULT * atr)
                take_profit = entry - (cfg.TP_ATR_MULT * atr)

            volume = self.risk_mgr.calculate_position_size(entry, stop_loss, symbol=symbol)

            logger.info(
                f"{symbol} Signal: {'BUY' if signal > 0 else 'SELL'} "
                f"| Confidence: {confidence:.2%} | Volume: {volume} "
                f"| ATR: {atr:.5f} ({atr/pip_value:.1f} pips) "
                f"| SL: {abs(entry-stop_loss)/pip_value:.1f} pips"
            )

            await self.trader.submit_order(
                symbol=symbol,
                direction=signal,
                volume=volume,
                entry_price=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=confidence
            )

        except Exception as e:
            logger.error(f"Symbol processing error ({symbol}): {e}", exc_info=True)

    async def update_trailing_stops(self):
        """Adjust SL for profitable trades using a live price pulled directly
        from MT5Trader, with a short-lived local cache fallback for a single
        transient tick-fetch failure."""
        missing_price_symbols = []

        for order_id, pos in list(self.trader.open_positions.items()):
            symbol = pos['symbol']
            current_price = self.trader.get_current_price(symbol)

            if current_price is not None:
                self._price_cache[symbol] = current_price
                self._price_cache_updated_at[symbol] = datetime.now()
            else:
                current_price = self._price_cache.get(symbol)

            if current_price is None:
                missing_price_symbols.append(symbol)
                # Never had a successful cache write — measure staleness from
                # when the position opened rather than from an unset time.
                last_known = self._price_cache_updated_at.get(symbol, pos['opened_at'])
                stale_minutes = (datetime.now() - last_known).total_seconds() / 60
                if stale_minutes >= cfg.PRICE_CACHE_STALE_ALERT_MINUTES:
                    last_alert = self._price_cache_last_alert.get(symbol)
                    should_alert = (
                        last_alert is None
                        or (datetime.now() - last_alert).total_seconds() / 60
                        >= cfg.PRICE_CACHE_STALE_ALERT_MINUTES
                    )
                    if should_alert:
                        logger.error(
                            f"No cached price for {symbol} (#{order_id}) in {stale_minutes:.1f} min "
                            f"— trailing-stop protection is not active for this position"
                        )
                        await self.telegram.send_alert(
                            f"⚠️ <b>NO PRICE DATA</b> — {symbol} #{order_id}\n"
                            f"No cached price for {stale_minutes:.1f} min. Trailing-stop protection "
                            f"is not active for this position."
                        )
                        self._price_cache_last_alert[symbol] = datetime.now()
                continue

            self._price_cache_last_alert.pop(symbol, None)

            profit = (
                (current_price - pos['entry']) * pos['volume']
                if pos['direction'] > 0
                else (pos['entry'] - current_price) * pos['volume']
            )

            initial_risk = abs(pos['entry'] - pos['sl']) * pos['volume']
            if profit > 2 * initial_risk:
                pip_value = cfg.SYMBOLS[symbol]['pip_value']
                lock_offset = cfg.TRAILING_STOP_LOCK_PIPS * pip_value
                new_sl = pos['entry'] + (lock_offset if pos['direction'] > 0 else -lock_offset)
                current_sl = pos['sl']
                # Only move SL in the profitable direction, never backwards
                sl_improved = (
                    (pos['direction'] > 0 and new_sl > current_sl) or
                    (pos['direction'] < 0 and new_sl < current_sl)
                )
                if sl_improved:
                    if await self.trader.modify_position_sl(order_id, new_sl):
                        logger.info(
                            f"Trailing SL #{order_id} {symbol}: {current_sl:.5f} → {new_sl:.5f}"
                        )
                    else:
                        logger.warning(f"Failed to update trailing SL for #{order_id}")

        if missing_price_symbols:
            logger.warning(
                f"{len(missing_price_symbols)} open position(s) with no cached price: "
                f"{missing_price_symbols}"
            )

    async def send_daily_report(self):
        """Send end-of-day metrics via Telegram."""
        trades_today = [t for t in self.trader.trade_log if t['duration'] < 24]
        # Trades whose P&L couldn't be confirmed (see get_closed_pnl) are excluded
        # from these stats rather than counted as zero.
        known_pnl_trades = [t for t in trades_today if t['pnl'] is not None]
        unknown_pnl_count = len(trades_today) - len(known_pnl_trades)

        wins = len([t for t in known_pnl_trades if t['pnl'] > 0])
        losses = len(known_pnl_trades) - wins
        total_pnl = sum(t['pnl'] for t in known_pnl_trades)

        report = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'pnl': total_pnl,
            'pnl_pct': (total_pnl / self.risk_mgr.config.account_equity * 100)
                       if self.risk_mgr.config.account_equity else 0,
            'total_trades': len(trades_today),
            'unknown_pnl_trades': unknown_pnl_count,
            'wins': wins,
            'losses': losses,
            'win_rate': (wins / len(known_pnl_trades)) if known_pnl_trades else 0,
            'max_win': max((t['pnl'] for t in known_pnl_trades if t['pnl'] > 0), default=0),
            'max_loss': min((t['pnl'] for t in known_pnl_trades if t['pnl'] < 0), default=0),
            'equity': self.risk_mgr.config.account_equity,
        }

        await self.telegram.send_daily_report(report)


async def main():
    load_dotenv()

    config = {
        'postgresql': cfg.POSTGRESQL,
        'mt5_account': os.getenv('MT5_ACCOUNT', '123456789'),
        'mt5_password': os.getenv('MT5_PASSWORD', 'password'),
        'mt5_server': os.getenv('MT5_SERVER', 'Exness-MT5'),
        'telegram_token': os.getenv('TELEGRAM_BOT_TOKEN', ''),
        'telegram_chat_id': os.getenv('TELEGRAM_CHAT_ID', ''),
        'initial_capital': 10000.0,
        'risk_per_trade': 0.01,
        'max_daily_loss': 0.05,
        'max_open_trades': 10,
        'symbols': cfg.ACTIVE_SYMBOLS,
        'mock_mode': True,   # bridge mode — no live candle feed; relaxes 48h staleness limit
    }

    system = TradingSystem(config)
    try:
        await system.run()
    except KeyboardInterrupt:
        logger.info("System shutting down...")
        system.trader.shutdown()
        await system.telegram.shutdown()
        db.close_all()


if __name__ == '__main__':
    _configure_logging()
    asyncio.run(main())
