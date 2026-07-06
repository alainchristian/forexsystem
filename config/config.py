"""
Configuration Module - Phase 1 Forex Trading System
Centralized settings for data pipeline, backtesting, and infrastructure
"""

import os
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Load .env file
load_dotenv(Path(__file__).parent.parent / '.env')

# Base paths
BASE_DIR = Path(__file__).parent.parent
SRC_DIR = BASE_DIR / 'src'
MODELS_DIR = BASE_DIR / 'models'
LOGS_DIR = BASE_DIR / 'logs'
DATA_DIR = BASE_DIR / 'data'
CONFIG_DIR = BASE_DIR / 'config'

# Ensure directories exist
for directory in [MODELS_DIR, LOGS_DIR, DATA_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# Per-symbol feature scalers persisted by train_models.py, promoted by
# model_versioning.promote() alongside the model artifacts so a rollback can
# never leave scalers out of sync with the live model. main.py refuses to
# trade any symbol whose scaler is missing here.
FEATURE_SCALER_DIR = MODELS_DIR / 'scalers'

# ============================================================================
# DATABASE CONFIGURATION
# ============================================================================

POSTGRESQL = {
    'dbname': os.getenv('FOREX_DB_NAME', 'forex_trading_db'),
    'user': os.getenv('FOREX_DB_USER', 'forex_user'),
    'password': os.getenv('FOREX_DB_PASSWORD', 'change_me_in_production'),
    'host': os.getenv('FOREX_DB_HOST', 'localhost'),
    'port': int(os.getenv('FOREX_DB_PORT', 5432))
}

REDIS = {
    'host': os.getenv('REDIS_HOST', 'localhost'),
    'port': int(os.getenv('REDIS_PORT', 6379)),
    'db': 0,
    'decode_responses': True,
    'socket_connect_timeout': 5,
    'socket_keepalive': True
}

# ============================================================================
# MT5 CONFIGURATION (Update before live trading)
# ============================================================================

MT5 = {
    'path': os.getenv('MT5_PATH', 'C:\\Program Files\\MetaTrader 5\\terminal64.exe'),
    'timeout': 5000,  # milliseconds
}

# Broker credentials - SET VIA ENVIRONMENT VARIABLES
MT5_CREDENTIALS = {
    'account': int(os.getenv('MT5_ACCOUNT', 0)),
    'password': os.getenv('MT5_PASSWORD', ''),
    'server': os.getenv('MT5_SERVER', 'Exness-MT5'),  # Change for your broker
}

# ============================================================================
# TRADING SYMBOLS & TIMEFRAMES
# ============================================================================

SYMBOLS = {
    # --- Majors (USD pairs) ---
    'EURUSD': {
        'timeframes': [240, 1440],  # 4H, Daily (in minutes)
        'pip_value': 0.0001,
        'max_spread': 0.0003,  # 3 pips typical
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'GBPUSD': {
        'timeframes': [240, 1440],
        'pip_value': 0.0001,
        'max_spread': 0.0004,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'USDJPY': {
        'timeframes': [240, 1440],
        'pip_value': 0.01,
        'max_spread': 0.05,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'AUDUSD': {
        'timeframes': [240, 1440],
        'pip_value': 0.0001,
        'max_spread': 0.0004,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'USDCAD': {
        'timeframes': [240, 1440],
        'pip_value': 0.0001,
        'max_spread': 0.0004,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'NZDUSD': {
        'timeframes': [240, 1440],
        'pip_value': 0.0001,
        'max_spread': 0.0005,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'USDCHF': {
        'timeframes': [240, 1440],
        'pip_value': 0.0001,
        'max_spread': 0.0004,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    # --- Major crosses ---
    'EURGBP': {
        'timeframes': [240, 1440],
        'pip_value': 0.0001,
        'max_spread': 0.0005,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'EURJPY': {
        'timeframes': [240, 1440],
        'pip_value': 0.01,
        'max_spread': 0.06,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'GBPJPY': {
        'timeframes': [240, 1440],
        'pip_value': 0.01,
        'max_spread': 0.08,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    # --- Non-USD crosses (round 1): commodity currencies ---
    # No USD/EUR/GBP/JPY exposure, added so the model has instruments to
    # express a view that isn't mechanically tied to USD direction. Note:
    # 3 of these 4 share AUD, so they're not independent of each other -
    # see the "round 2" crosses below, added specifically to avoid piling
    # further onto USD or AUD.
    'AUDCAD': {
        'timeframes': [240, 1440],
        'pip_value': 0.0001,
        'max_spread': 0.0006,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'AUDNZD': {
        'timeframes': [240, 1440],
        'pip_value': 0.0001,
        'max_spread': 0.0007,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'AUDCHF': {
        'timeframes': [240, 1440],
        'pip_value': 0.0001,
        'max_spread': 0.0006,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'CADCHF': {
        'timeframes': [240, 1440],
        'pip_value': 0.0001,
        'max_spread': 0.0006,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    # --- Non-USD crosses (round 2): fill in EUR/GBP/JPY/CAD/CHF/NZD ---
    # None of these touch USD or AUD, the two currencies already most
    # represented above. Brings currency exposure across all 24 symbols to
    # USD=7, CAD=7, CHF=7, EUR=6, NZD=6, JPY=6, GBP=5, AUD=4 - flatter than
    # the 7-vs-2 (USD-vs-NZD) split before this round.
    'EURCAD': {
        'timeframes': [240, 1440],
        'pip_value': 0.0001,
        'max_spread': 0.0005,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'EURCHF': {
        'timeframes': [240, 1440],
        'pip_value': 0.0001,
        'max_spread': 0.0004,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'EURNZD': {
        'timeframes': [240, 1440],
        'pip_value': 0.0001,
        'max_spread': 0.0008,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'GBPCAD': {
        'timeframes': [240, 1440],
        'pip_value': 0.0001,
        'max_spread': 0.0008,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'GBPCHF': {
        'timeframes': [240, 1440],
        'pip_value': 0.0001,
        'max_spread': 0.0007,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'CADJPY': {
        'timeframes': [240, 1440],
        'pip_value': 0.01,
        'max_spread': 0.06,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'CHFJPY': {
        'timeframes': [240, 1440],
        'pip_value': 0.01,
        'max_spread': 0.08,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'NZDJPY': {
        'timeframes': [240, 1440],
        'pip_value': 0.01,
        'max_spread': 0.08,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'NZDCAD': {
        'timeframes': [240, 1440],
        'pip_value': 0.0001,
        'max_spread': 0.0009,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'NZDCHF': {
        'timeframes': [240, 1440],
        'pip_value': 0.0001,
        'max_spread': 0.0008,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
}

ACTIVE_SYMBOLS = list(SYMBOLS.keys())
ALLOWED_SYMBOLS = set(SYMBOLS.keys())

# ============================================================================
# TRADING BEHAVIOUR CONFIGURATION
# ============================================================================

# ATR multipliers for SL/TP calculation (widened from 2/3 to reduce premature exits)
SL_ATR_MULT: float = 3.0
TP_ATR_MULT: float = 4.5

# Entry slippage applied at order time to align backtest with live conditions
ENTRY_SLIP_PIPS: float = 0.00015  # 1.5 pips

# Minimum seconds between trades on the same symbol (1 full 4H candle)
TRADE_COOLDOWN_SECONDS: int = 14_400

# Minimum ensemble confidence required to open a trade
ENSEMBLE_CONFIDENCE_THRESHOLD: float = 0.55

# UTC hour at which the daily P&L report is sent and stats reset
DAILY_RESET_HOUR_UTC: int = 20

# Ranked Replacement guardrails: when max_open_trades is reached, a new
# signal may bump the lowest-confidence open position only if ALL hold.
# Prevents high-frequency churn when confidence hovers near the threshold,
# and prevents realising a loss on a position purely to make room for a
# new signal, regardless of how much stronger that signal is.
MIN_REPLACEMENT_HOLD_MINUTES: float = 10.0
MIN_REPLACEMENT_CONFIDENCE_GAP: float = 0.07
MIN_REPLACEMENT_PROFIT: float = 0.0  # position must not be at a loss to be replaced

# P&L reconciliation: how often (in reconciliation attempts, ~1 per main-loop
# tick) to re-alert on Telegram while a closed position's P&L is still
# unconfirmed. Kept low — a daily-loss breaker silently running on
# incomplete data is worse than a noisy alert.
PNL_RECONCILE_ALERT_INTERVAL: int = 5

# Once any pending_pnl entry (see mt5_trader.queue_pnl_reconciliation) has been
# unconfirmed longer than this, can_open_trade() hard-blocks new trades rather
# than just alerting — the daily-loss/drawdown breakers read daily_pnl, which
# is unreliable while a close is unreconciled. This is a hard AGE CUTOFF (new
# trades resume once the entry ages back out or reconciles), not a "block
# until reconciled" policy — flag to Alain if the latter is preferred instead.
MAX_UNCONFIRMED_PNL_AGE_MINUTES: float = 15.0

# How long to wait for the SignalBridge EA to write a result.json response to
# a bridge-mode order/CLOSE/MODIFY_SL signal before treating it as failed or
# timed out. Used by submit_order, close_position, and modify_position_sl so
# all three bridge actions behave consistently.
BRIDGE_RESULT_TIMEOUT_SECONDS: int = 30

# Kelly-fraction clamps used by calculate_kelly_fraction() (execution_logic.py).
# Both risk_manager.py and backtester.py share the same underlying formula,
# but apply the result differently, so they keep separate clamp ranges:
# risk_manager.py uses its pair as a *multiplier* on a flat 1%-of-equity base
# position size; backtester.py uses its pair *directly* as the risk-fraction
# of capital. Sharing one clamp range between the two would silently change
# real sizing behavior in whichever place didn't originally use it.
RISK_KELLY_MULTIPLIER_MIN: float = 0.5   # 0.5x-1.5x Kelly multiplier (risk_manager.py)
RISK_KELLY_MULTIPLIER_MAX: float = 1.5
BACKTEST_KELLY_FRACTION_MIN: float = 0.01  # 1%-20% of capital directly (backtester.py)
BACKTEST_KELLY_FRACTION_MAX: float = 0.20

# Trailing stop: once profit exceeds 2x initial risk, lock in this many pips
# beyond entry (in the trade's favor). Previously a flat 0.005 price-unit
# offset — 50 pips on non-JPY pairs, 0.5 pips on JPY pairs — this makes the
# lock-in distance consistent in pips across all pair types.
TRAILING_STOP_LOCK_PIPS: float = 5.0

# Bridge mode has no live spread data today — SignalBridge.mq5 (on the VPS,
# not in this repo) doesn't report it. Keep this False until that EA is
# updated to write a per-symbol tick/spread file; flipping it on before then
# would reject every single bridge-mode trade (fail-loud with no data available
# is the intended behavior once the EA is updated, but not before).
BRIDGE_SPREAD_CHECK_ENABLED: bool = False

# Trailing-stop price cache (main.py _price_cache): once an open position has
# gone this long without a successfully cached price (Redis down and no local
# fallback value yet), repeat a Telegram alert — trailing-stop protection is
# silently inactive for that position until a price is available again.
PRICE_CACHE_STALE_ALERT_MINUTES: float = 15.0

# ============================================================================
# DATA PIPELINE CONFIGURATION
# ============================================================================

DATA_CONFIG = {
    'historical_days': 730,  # 2 years of historical data
    'batch_size': 1000,      # Insert records in batches
    'cache_ttl': 3600,       # Redis cache 1 hour
    'update_interval': 300,  # Update every 5 minutes (seconds)
    'retry_attempts': 3,
    'retry_delay': 5,        # seconds
}

# ============================================================================
# FEATURE ENGINEERING CONFIGURATION
# ============================================================================

FEATURES = {
    'technical_indicators': {
        'rsi_period': 14,
        'macd_fast': 12,
        'macd_slow': 26,
        'macd_signal': 9,
        'atr_period': 14,
        'bb_period': 20,
        'sma_periods': [20, 50, 200],
    },
    'volume_indicators': {
        'volume_sma_period': 20,
    },
    'volatility': {
        'vol_period': 20,
        'vol_lookback': 100,
    },
    'normalize': True,  # StandardScaler normalization
}

# ============================================================================
# BACKTEST CONFIGURATION
# ============================================================================

BACKTEST = {
    'initial_capital': 10000.0,
    'initial_margin': 0.02,  # 2% per trade (Kelly-adjusted)
    'slippage_pips': 1.5,
    'commission_per_trade': 1.5,  # Fixed USD
    'risk_free_rate': 0.02,  # Annual
    'walk_forward': {
        'train_period': 252,  # 252 trading days = 1 year
        'test_period': 63,    # 63 trading days = 3 months
    }
}

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        },
        'detailed': {
            'format': '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
        }
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'INFO',
            'formatter': 'standard',
            'stream': 'ext://sys.stdout'
        },
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'level': 'DEBUG',
            'formatter': 'detailed',
            'filename': str(LOGS_DIR / 'forex_system.log'),
            'maxBytes': 10485760,  # 10MB
            'backupCount': 7
        }
    },
    'loggers': {
        'httpx': {'level': 'WARNING'},
        'httpcore': {'level': 'WARNING'},
        'hpack': {'level': 'WARNING'},
        'h2': {'level': 'WARNING'},
        'telegram': {'level': 'WARNING'},
        'urllib3': {'level': 'WARNING'},
    },
    'root': {
        'level': 'DEBUG',
        'handlers': ['console', 'file']
    }
}

# ============================================================================
# ENVIRONMENT VALIDATION
# ============================================================================

def validate_config():
    """Validate critical configuration at startup"""
    issues = []
    
    if POSTGRESQL['password'] == 'change_me_in_production':
        issues.append("⚠️  WARNING: PostgreSQL password not set. Update FOREX_DB_PASSWORD env var")
    
    if not DATA_DIR.exists():
        issues.append(f"❌ Data directory missing: {DATA_DIR}")
    
    return issues

if __name__ == '__main__':
    print("Configuration loaded successfully")
    issues = validate_config()
    if issues:
        for issue in issues:
            print(issue)
