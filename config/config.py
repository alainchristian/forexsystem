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
# signal may bump the lowest-confidence open position only if BOTH hold.
# Prevents high-frequency churn when confidence hovers near the threshold.
MIN_REPLACEMENT_HOLD_MINUTES: float = 10.0
MIN_REPLACEMENT_CONFIDENCE_GAP: float = 0.07

# P&L reconciliation: how often (in reconciliation attempts, ~1 per main-loop
# tick) to re-alert on Telegram while a closed position's P&L is still
# unconfirmed. Kept low — a daily-loss breaker silently running on
# incomplete data is worse than a noisy alert.
PNL_RECONCILE_ALERT_INTERVAL: int = 5

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
