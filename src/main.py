import asyncio
import logging
import os
from datetime import datetime, time
from typing import Dict
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

# Make sure we can import from src
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.risk_manager import RiskManager, RiskConfig
from src.telegram_bot import TelegramNotifier
from src.mt5_trader import MT5Trader

# Import models
from src.models.lstm_predictor import LSTMPredictor
from src.models.xgboost_classifier import XGBoostSignal
from src.models.ensemble import EnsembleStrategy

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(str(Path(__file__).parent.parent / 'logs' / 'main.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('Main')

class TradingSystem:
    def __init__(self, config: Dict):
        self.config = config
        
        # Risk Manager
        self.risk_mgr = RiskManager(RiskConfig(
            account_equity=config['initial_capital'],
            risk_per_trade=config['risk_per_trade'],
            max_daily_loss_pct=config['max_daily_loss'],
            max_open_trades=config['max_open_trades']
        ))
        
        # Telegram Notifier
        self.telegram = TelegramNotifier(
            bot_token=config['telegram_token'],
            chat_id=config['telegram_chat_id']
        )
        
        # MT5 Trader
        self.trader = MT5Trader(
            account=config['mt5_account'],
            password=config['mt5_password'],
            server=config['mt5_server'],
            risk_manager=self.risk_mgr,
            telegram_notifier=self.telegram
        )
        
        # Models
        self.lstm = LSTMPredictor(lookback=60)
        
        # Load pre-trained models if they exist. In a real scenario, you'd ensure they are trained.
        model_dir = str(Path(__file__).parent.parent / 'models')
        try:
            self.lstm.load(model_dir)
            self.xgb = XGBoostSignal()
            self.xgb.load(model_dir)
            logger.info("Models loaded successfully")
        except Exception as e:
            logger.warning(f"Could not load pre-trained models. Have you trained them? Error: {e}")
            self.xgb = XGBoostSignal()
            
        self.ensemble = EnsembleStrategy(self.lstm, self.xgb, threshold_confidence=0.85)
    
    async def run(self):
        """Main trading loop"""
        await self.telegram.initialize()
        await self.telegram.setup_controls(self.trader)
        
        if not self.trader.initialize():
            logger.error("Failed to initialize MT5")
            # For testing, we might proceed anyway if MT5 is disabled
            if not self.config.get('mock_mode', False):
                return
        else:
            # Sync risk manager with actual account balance
            acc_info = self.trader.get_account_info()
            if acc_info and 'equity' in acc_info:
                logger.info(f"Syncing Risk Manager with live MT5 equity: ${acc_info['equity']:.2f}")
                self.risk_mgr.config.account_equity = acc_info['equity']
                self.risk_mgr.peak_equity = acc_info['equity']
        
        logger.info("Trading system initialized, entering main loop...")
        await self.telegram.send_alert("🚀 <b>Trading System Started</b>")
        
        while True:
            try:
                # Bot is currently set to trade 24/7
                current_time = datetime.utcnow().time()
                
                # Process each symbol
                for symbol in self.config['symbols']:
                    await self.process_symbol(symbol)
                
                # Check for trailing stop updates
                await self.update_trailing_stops()
                
                # Daily reporting at 9 PM UTC (5 PM ET)
                if current_time >= time(20, 55) and current_time < time(20, 56):
                    await self.send_daily_report()
                    self.risk_mgr.reset_daily_stats()
                
                await asyncio.sleep(60)  # Check every minute
            
            except Exception as e:
                logger.error(f"Main loop exception: {e}", exc_info=True)
                await self.telegram.send_alert(f"⚠️ Error: {str(e)[:100]}")
                await asyncio.sleep(60)
    
    async def process_symbol(self, symbol: str):
        """Analyze and trade single symbol"""
        import psycopg2
        from src.features import FeatureEngine
        
        try:
            # Fetch recent 4H candles
            db = psycopg2.connect(**self.config['postgresql'])
            cursor = db.cursor()
            
            cursor.execute(f"""
                SELECT timestamp, open, high, low, close, volume 
                FROM ohlcv_{symbol.lower()} 
                ORDER BY timestamp DESC LIMIT 200
            """)
            rows = cursor.fetchall()
            db.close()
            
            if len(rows) < 150:
                logger.warning(f"Insufficient data for {symbol}")
                return
            
            df = pd.DataFrame(rows[::-1], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # Feature engineering
            fe = FeatureEngine(df)
            fe.add_technical_indicators() \
              .add_price_action_features() \
              .add_market_microstructure() \
              .normalize()
            
            # Generate signal
            recent_data = fe.features_normalized.iloc[-60:].values
            current_price = df['close'].iloc[-1]
            
            signal, confidence = self.ensemble.generate_signal(recent_data, current_price)
            
            if signal == 0:
                logger.debug(f"{symbol}: No signal (confidence: {confidence:.2%})")
                return
            
            # Calculate trade levels
            atr = fe.features['atr_14'].iloc[-1]
            current_price = df['close'].iloc[-1]
            
            if signal == 1:  # BUY
                entry = current_price
                stop_loss = entry - (2 * atr)
                take_profit = entry + (3 * atr)
            else:  # SELL
                entry = current_price
                stop_loss = entry + (2 * atr)
                take_profit = entry - (3 * atr)
            
            # Position sizing
            volume = self.risk_mgr.calculate_position_size(entry, stop_loss)
            
            logger.info(f"{symbol} Signal: {'BUY' if signal > 0 else 'SELL'} "
                       f"| Confidence: {confidence:.2%} | Volume: {volume}")
            
            # Submit order
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
        """Adjust SL for profitable trades"""
        import redis
        
        try:
            r = redis.Redis(host=os.getenv('REDIS_HOST', 'localhost'), port=int(os.getenv('REDIS_PORT', 6379)), decode_responses=True)
            
            for order_id, pos in list(self.trader.open_positions.items()):
                # Get current price
                current_price_str = r.get(f"{pos['symbol']}:240:latest_price")
                if not current_price_str:
                    continue
                
                current_price = float(current_price_str)
                profit = (current_price - pos['entry']) * pos['volume'] if pos['direction'] > 0 \
                        else (pos['entry'] - current_price) * pos['volume']
                
                # Move SL if profit > 2x risk
                initial_risk = abs(pos['entry'] - pos['sl']) * pos['volume']
                if profit > 2 * initial_risk:
                    # Move SL to break-even + small pip increment
                    new_sl = pos['entry'] + (0.005 if pos['direction'] > 0 else -0.005)
                    
                    # Log it
                    logger.info(f"Trailing SL for #{order_id}: {new_sl:.5f} (Current Price: {current_price})")
        except Exception as e:
            logger.warning(f"Could not update trailing stops (Redis might be down): {e}")
    
    async def send_daily_report(self):
        """Send end-of-day metrics"""
        trades_today = [t for t in self.trader.trade_log if t['duration'] < 24]
        
        wins = len([t for t in trades_today if t['pnl'] > 0])
        losses = len(trades_today) - wins
        total_pnl = sum([t['pnl'] for t in trades_today])
        
        report = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'pnl': total_pnl,
            'pnl_pct': (total_pnl / self.risk_mgr.config.account_equity * 100) if self.risk_mgr.config.account_equity else 0,
            'total_trades': len(trades_today),
            'wins': wins,
            'losses': losses,
            'win_rate': (wins / len(trades_today)) if trades_today else 0,
            'max_win': max([t['pnl'] for t in trades_today if t['pnl'] > 0], default=0),
            'max_loss': min([t['pnl'] for t in trades_today if t['pnl'] < 0], default=0),
            'equity': self.risk_mgr.config.account_equity
        }
        
        await self.telegram.send_daily_report(report)

async def main():
    load_dotenv()
    
    config = {
        'postgresql': {
            'dbname': os.getenv('FOREX_DB_NAME', 'forex_trading_db'),
            'user': os.getenv('FOREX_DB_USER', 'admin'),
            'password': os.getenv('FOREX_DB_PASSWORD', 'admin'),
            'host': os.getenv('FOREX_DB_HOST', 'localhost'),
            'port': os.getenv('FOREX_DB_PORT', '5432')
        },
        'mt5_account': os.getenv('MT5_ACCOUNT', '123456789'),
        'mt5_password': os.getenv('MT5_PASSWORD', 'password'),
        'mt5_server': os.getenv('MT5_SERVER', 'Exness-MT5'),
        'telegram_token': os.getenv('TELEGRAM_BOT_TOKEN', ''),
        'telegram_chat_id': os.getenv('TELEGRAM_CHAT_ID', ''),
        'initial_capital': 10000.0,
        'risk_per_trade': 0.02,
        'max_daily_loss': 0.05,
        'max_open_trades': 3,
        'symbols': ['EURUSDm', 'GBPUSDm', 'USDJPYm'],
        'mock_mode': False
    }
    
    system = TradingSystem(config)
    try:
        await system.run()
    except KeyboardInterrupt:
        logger.info("System shutting down...")
        system.trader.shutdown()
        await system.telegram.shutdown()

if __name__ == '__main__':
    asyncio.run(main())
