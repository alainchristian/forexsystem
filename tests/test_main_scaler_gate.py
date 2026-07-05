import sys
import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from src.main import TradingSystem
from src.features import FeatureEngine


def _bare_trading_system(feature_scalers: dict):
    """Build a TradingSystem instance without running __init__ (which
    connects to Postgres/Redis/MT5/Telegram, loads ML models, and loads
    scalers from disk) - only process_symbol()'s scaler-gate behaviour is
    under test here."""
    system = object.__new__(TradingSystem)
    system.config = {'mock_mode': True, 'symbols': ['EURUSD']}
    system.feature_scalers = feature_scalers
    system.trader = MagicMock()
    system.trader.submit_order = AsyncMock(return_value=None)
    system.risk_mgr = MagicMock()
    system.ensemble = MagicMock()
    system._fetch_ohlcv = MagicMock()
    system._fetch_daily_closes = MagicMock()
    system._is_trend_aligned = MagicMock(return_value=True)
    return system


def _make_ohlcv(periods=200, seed=7):
    rng = np.random.default_rng(seed)
    close = 1.1000 + np.cumsum(rng.standard_normal(periods) * 0.0005)
    df = pd.DataFrame({
        'timestamp': pd.date_range(end=datetime.utcnow(), periods=periods, freq='4h'),
        'open': close + rng.standard_normal(periods) * 0.0001,
        'high': close + abs(rng.standard_normal(periods) * 0.0002) + 0.002,
        'low': close - abs(rng.standard_normal(periods) * 0.0002) - 0.002,
        'close': close,
        'volume': rng.integers(1000, 10000, periods),
    })
    return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]


def _make_daily_closes(periods=60):
    return pd.DataFrame({
        'timestamp': pd.date_range(end=datetime.utcnow(), periods=periods, freq='D'),
        'close': 1.10 + np.cumsum(np.random.default_rng(3).standard_normal(periods) * 0.001),
    })


def test_process_symbol_refuses_symbol_without_persisted_scaler():
    """A symbol missing a persisted feature scaler must never trade - the
    old behaviour (falling back to fit=True on live data) is exactly the
    train/serve skew bug this gate exists to prevent."""
    system = _bare_trading_system(feature_scalers={})  # no scaler for EURUSD

    asyncio.run(system.process_symbol("EURUSD"))

    system._fetch_ohlcv.assert_not_called()  # returns before even fetching data
    system.trader.submit_order.assert_not_called()


def test_process_symbol_transforms_with_loaded_scaler_not_a_fresh_fit():
    """When a scaler is present, process_symbol must call normalize(fit=False)
    using the persisted scaler, not fit a new one on the live window."""
    df = _make_ohlcv()

    # A real scaler fit on different (training-like) data, standing in for
    # the persisted training-time scaler.
    training_df = _make_ohlcv(periods=250, seed=99)
    training_engine = FeatureEngine(training_df)
    training_engine.add_technical_indicators() \
                   .add_price_action_features() \
                   .add_market_microstructure() \
                   .normalize()

    system = _bare_trading_system(feature_scalers={"EURUSD": training_engine.scaler})
    system._fetch_ohlcv.return_value = df
    system._fetch_daily_closes.return_value = _make_daily_closes()
    system.ensemble.generate_signal.return_value = (1, 0.9)
    system.risk_mgr.calculate_position_size.return_value = 0.1

    original_normalize = FeatureEngine.normalize
    calls = []

    def spy_normalize(self, method='standard', fit=True):
        calls.append(fit)
        return original_normalize(self, method=method, fit=fit)

    with patch.object(FeatureEngine, 'normalize', spy_normalize):
        asyncio.run(system.process_symbol("EURUSD"))

    assert calls == [False]
    system.trader.submit_order.assert_awaited_once()
