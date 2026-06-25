import asyncio
import logging
import logging.config
import sys
import os
from datetime import datetime, time
from typing import Dict
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

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

from src.models.lstm_predictor import LSTMPredictor
from src.models.xgboost_classifier import XGBoostSignal
from src.models.ensemble import EnsembleStrategy

logging.config.dictConfig(cfg.LOGGING)
logger = logging.getLogger('Main')


class TradingSystem:
    def __init__(self, config: Dict):
        self.config = config

        self.risk_mgr = RiskManager(RiskConfig(
            account_equity=config['initial_capital'],
            risk_per_trade=config['risk_per_trade'],
            max_daily_loss_pct=config['max_daily_loss'],
            max_open_trades=config['max_open_trades']
        ))

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

        # In-memory price cache used as Redis fallback for trailing stops
        self._price_cache: Dict[str, float] = {}
        self._redis_available: bool = True

    async def run(self):
        """Main trading loop"""
        await self.telegram.initialize()
        await self.telegram.setup_controls(self.trader)

        if not self.trader.initialize():
            logger.error("Failed to initialize MT5")
            if not self.config.get('mock_mode', False):
                return
        else:
            acc_info = self.trader.get_account_info()
            if acc_info and 'equity' in acc_info:
                logger.info(f"Syncing Risk Manager with live MT5 equity: ${acc_info['equity']:.2f}")
                self.risk_mgr.config.account_equity = acc_info['equity']
                self.risk_mgr.peak_equity = acc_info['equity']

            # Recover any positions that survived a previous crash
            await self._reconcile_positions()

        logger.info("Trading system initialized, entering main loop...")
        await self.telegram.send_alert("🚀 <b>Trading System Started</b>")

        while True:
            try:
                current_time = datetime.utcnow().time()

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

    async def _reconcile_positions(self):
        """On startup, rebuild open_positions from whatever MT5 has open."""
        mt5_positions = self.trader.get_open_positions()
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
                    'opened_at': datetime.fromtimestamp(pos.time),
                    'order_id': pos.ticket,
                    'confidence': cfg.ENSEMBLE_CONFIDENCE_THRESHOLD,
                }
                recovered += 1

        if recovered:
            logger.info(f"Reconciled {recovered} open position(s) from MT5")
            await self.telegram.send_alert(
                f"🔄 Recovered <b>{recovered}</b> open position(s) from previous session"
            )

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

        try:
            df = await asyncio.to_thread(self._fetch_ohlcv, symbol)
            if df is None or len(df) < 150:
                logger.warning(f"Insufficient data for {symbol}")
                return

            fe = FeatureEngine(df)
            fe.add_technical_indicators() \
              .add_price_action_features() \
              .add_market_microstructure() \
              .normalize()

            recent_data = fe.features_normalized.iloc[-60:].values
            current_price = df['close'].iloc[-1]

            signal, confidence = self.ensemble.generate_signal(recent_data, current_price)

            if signal == 0:
                logger.debug(f"{symbol}: No signal (confidence: {confidence:.2%})")
                return

            # ── Daily trend filter ────────────────────────────────────────────
            # Only trade WITH the Daily SMA(50) direction. This blocks the most
            # common failure mode: entering counter-trend on a 4H bounce.
            daily_df = await asyncio.to_thread(self._fetch_daily_closes, symbol)
            if daily_df is not None and not self._is_trend_aligned(daily_df, signal):
                dir_str = "BUY" if signal > 0 else "SELL"
                logger.info(
                    f"{symbol}: {dir_str} signal filtered out — "
                    f"against Daily SMA(50) trend (close={daily_df['close'].iloc[-1]:.5f})"
                )
                return
            # ─────────────────────────────────────────────────────────────────

            # ATR-based SL/TP — multipliers are configurable in config.py
            atr = fe.features['atr_14'].iloc[-1]

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
                f"| Confidence: {confidence:.2%} | Volume: {volume}"
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
        """Adjust SL for profitable trades using Redis price feed, with local cache fallback."""
        import redis

        r = None
        try:
            r = redis.Redis(**cfg.REDIS)
            r.ping()
            if not self._redis_available:
                logger.info("Redis reconnected — trailing stops resuming from live feed")
            self._redis_available = True
        except Exception:
            if self._redis_available:
                logger.warning("Redis unavailable — trailing stops falling back to local price cache")
            self._redis_available = False

        for order_id, pos in list(self.trader.open_positions.items()):
            symbol = pos['symbol']
            current_price = None

            if r is not None:
                try:
                    price_str = r.get(f"{symbol}:240:latest_price")
                    if price_str:
                        current_price = float(price_str)
                        self._price_cache[symbol] = current_price
                except Exception:
                    pass

            if current_price is None:
                current_price = self._price_cache.get(symbol)

            if current_price is None:
                continue

            profit = (
                (current_price - pos['entry']) * pos['volume']
                if pos['direction'] > 0
                else (pos['entry'] - current_price) * pos['volume']
            )

            initial_risk = abs(pos['entry'] - pos['sl']) * pos['volume']
            if profit > 2 * initial_risk:
                new_sl = pos['entry'] + (0.005 if pos['direction'] > 0 else -0.005)
                current_sl = pos['sl']
                # Only move SL in the profitable direction, never backwards
                sl_improved = (
                    (pos['direction'] > 0 and new_sl > current_sl) or
                    (pos['direction'] < 0 and new_sl < current_sl)
                )
                if sl_improved:
                    if self.trader.modify_position_sl(order_id, new_sl):
                        logger.info(
                            f"Trailing SL #{order_id} {symbol}: {current_sl:.5f} → {new_sl:.5f}"
                        )
                    else:
                        logger.warning(f"Failed to update trailing SL for #{order_id}")

    async def send_daily_report(self):
        """Send end-of-day metrics via Telegram."""
        trades_today = [t for t in self.trader.trade_log if t['duration'] < 24]

        wins = len([t for t in trades_today if t['pnl'] > 0])
        losses = len(trades_today) - wins
        total_pnl = sum(t['pnl'] for t in trades_today)

        report = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'pnl': total_pnl,
            'pnl_pct': (total_pnl / self.risk_mgr.config.account_equity * 100)
                       if self.risk_mgr.config.account_equity else 0,
            'total_trades': len(trades_today),
            'wins': wins,
            'losses': losses,
            'win_rate': (wins / len(trades_today)) if trades_today else 0,
            'max_win': max((t['pnl'] for t in trades_today if t['pnl'] > 0), default=0),
            'max_loss': min((t['pnl'] for t in trades_today if t['pnl'] < 0), default=0),
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
        'mock_mode': False,
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
    asyncio.run(main())
