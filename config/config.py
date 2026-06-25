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
    'EURUSDm': {
        'timeframes': [240, 1440],  # 4H, Daily (in minutes)
        'pip_value': 0.0001,
        'max_spread': 0.0003,  # 3 pips typical
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'GBPUSDm': {
        'timeframes': [240, 1440],
        'pip_value': 0.0001,
        'max_spread': 0.0004,
        'min_lot': 0.01,
        'max_lot': 100.0
    },
    'USDJPYm': {
        'timeframes': [240, 1440],
        'pip_value': 0.01,
        'max_spread': 0.05,
        'min_lot': 0.01,
        'max_lot': 100.0
    }
}

ACTIVE_SYMBOLS = list(SYMBOLS.keys())

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
        'root': {
            'level': 'DEBUG',
            'handlers': ['console', 'file']
        }
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
