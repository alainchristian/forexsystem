import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from src.features import FeatureEngine


def _make_ohlcv(periods=300, seed=42):
    dates = pd.date_range(start='2023-01-01', periods=periods, freq='4h')
    rng = np.random.default_rng(seed)
    close = 1.0500 + np.cumsum(rng.standard_normal(periods) * 0.0005)
    df = pd.DataFrame({
        'timestamp': dates,
        'open': close + rng.standard_normal(periods) * 0.0002,
        'high': close + abs(rng.standard_normal(periods) * 0.0003),
        'low': close - abs(rng.standard_normal(periods) * 0.0003),
        'close': close,
        'volume': rng.integers(1000, 10000, periods),
    })
    return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]


def _built_engine(df: pd.DataFrame) -> FeatureEngine:
    engine = FeatureEngine(df)
    engine.add_technical_indicators() \
          .add_price_action_features() \
          .add_market_microstructure()
    return engine


def test_save_load_scaler_roundtrips_transform_only(tmp_path):
    """A scaler saved after training and reloaded at inference time must
    transform identically to the original fit — this is the fix for feeding
    live predictions a scaler fit on a completely different (and much
    smaller) distribution than training used."""
    df = _make_ohlcv()

    engine = _built_engine(df)
    engine.normalize()  # fit=True, as train_models.py does
    original = engine.features_normalized.copy()

    scaler_path = tmp_path / "EURUSD.pkl"
    engine.save_scaler(str(scaler_path))

    # A second, independent engine on the same data loads the persisted
    # scaler and transforms only (fit=False) - must reproduce the original
    # fit_transform() output bit-for-bit, not just approximately.
    engine2 = _built_engine(df)
    engine2.load_scaler(str(scaler_path))
    engine2.normalize(fit=False)

    pd.testing.assert_frame_equal(original, engine2.features_normalized)


def test_transform_only_differs_from_fresh_fit_on_different_data():
    """Regression guard: transforming dataset B with a scaler fit on dataset
    A must differ from fitting a fresh scaler directly on B. If this ever
    starts passing with equal output, fit=False silently stopped using the
    persisted scaler's statistics (i.e. the skew bug crept back in)."""
    df_a = _make_ohlcv(seed=1)
    df_b = _make_ohlcv(seed=2)

    engine_a = _built_engine(df_a)
    engine_a.normalize()

    transform_only = _built_engine(df_b)
    transform_only.scaler = engine_a.scaler
    transform_only.normalize(fit=False)

    fresh_fit = _built_engine(df_b)
    fresh_fit.normalize()

    assert not np.allclose(
        transform_only.features_normalized.values,
        fresh_fit.features_normalized.values,
    )


def test_save_scaler_before_fit_raises(tmp_path):
    df = _make_ohlcv()
    engine = _built_engine(df)

    try:
        engine.save_scaler(str(tmp_path / "x.pkl"))
        assert False, "expected ValueError for an unfit scaler"
    except ValueError:
        pass
