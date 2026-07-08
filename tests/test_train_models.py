import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from src.train_models import train_lstm, train_xgboost


def _make_symbol_df(base_price, n):
    dates = pd.date_range('2023-01-01', periods=n, freq='4h')
    close = base_price + np.cumsum(np.random.default_rng(int(base_price)).normal(0, base_price * 0.001, n))
    return pd.DataFrame({'timestamp': dates, 'close': close})


class _FakeLSTM:
    """Stands in for LSTMPredictor: records the length of every df passed to
    prepare_data() so we can prove it's called once per symbol, not once on
    a concatenated multi-symbol blob."""
    def __init__(self):
        self.prepare_data_calls = []
        self.model = _FakeKerasModel()

    def prepare_data(self, df, features_df, test_size):
        self.prepare_data_calls.append(len(df))
        n = len(df) - 5  # pretend lookback=5
        n_train = int(n * (1 - test_size))
        X = np.zeros((n, 5, features_df.shape[1]))
        y = np.zeros(n)
        return (X[:n_train], y[:n_train]), (X[n_train:], y[n_train:])

    def train(self, X_train, y_train, epochs, batch_size):
        self.trained_on_shape = X_train.shape


class _FakeKerasModel:
    def evaluate(self, X_test, y_test, verbose=0):
        return (0.1, 0.05)


class _FakeXGB:
    """Stands in for XGBoostSignal: records the length of every df passed to
    prepare_labels() so we can prove it's called once per symbol, not once
    on a concatenated multi-symbol blob (whose lookahead window would
    otherwise read into the next symbol's prices near the join)."""
    def __init__(self):
        self.prepare_labels_calls = []

    def prepare_labels(self, df, lookahead, threshold_pct):
        self.prepare_labels_calls.append(len(df))
        return np.zeros(len(df))

    def train(self, X, y, cv_folds, feature_names, groups=None, embargo=None):
        self.trained_on_shape = X.shape
        self.groups = groups
        return {'accuracy': 0.5, 'precision': 0.5}


def test_train_lstm_windows_each_symbol_independently():
    """prepare_data() must be called once per symbol with that symbol's own
    row count, never once with a combined multi-symbol row count - that
    would let lookback windows near the join splice together candles from
    two different currency pairs."""
    fake = _FakeLSTM()
    df_a, df_b = _make_symbol_df(1.0, 50), _make_symbol_df(100.0, 80)
    feat_a = pd.DataFrame(np.zeros((50, 3)))
    feat_b = pd.DataFrame(np.zeros((80, 3)))

    metrics = train_lstm(fake, [df_a, df_b], [feat_a, feat_b])

    assert fake.prepare_data_calls == [50, 80]
    assert metrics == {"val_loss": 0.1, "val_mae": 0.05}
    # combined train set = sum of each symbol's own 80% split
    expected_train_rows = int((50 - 5) * 0.8) + int((80 - 5) * 0.8)
    assert fake.trained_on_shape[0] == expected_train_rows


def test_train_lstm_shuffles_but_preserves_xy_pairing():
    """The combined training set is shuffled once before fit() so Keras's
    internal validation_split (which takes the last 10% of whatever order
    the array is already in, before any shuffling) doesn't just see
    whichever symbol landed last in the concatenation - but shuffling X and
    y together must never desync a window from its own label."""
    lookback = 5

    class PairingFakeLSTM:
        def __init__(self):
            self.model = _FakeKerasModel()

        def prepare_data(self, df, features_df, test_size):
            n = len(df) - lookback
            n_train = int(n * (1 - test_size))
            # Bake each window's "identity" (constant per symbol) into both
            # its features and its label, so we can verify after shuffling
            # that a window's X still matches its own y.
            ids = features_df.iloc[lookback:, 0].values
            X = np.zeros((n, lookback, features_df.shape[1]))
            for i in range(n):
                X[i] = features_df.iloc[i:i + lookback].values
            y = ids.copy()
            return (X[:n_train], y[:n_train]), (X[n_train:], y[n_train:])

        def train(self, X_train, y_train, epochs, batch_size):
            self.X_train, self.y_train = X_train, y_train

    fake = PairingFakeLSTM()
    df_a, df_b = _make_symbol_df(1.0, 50), _make_symbol_df(100.0, 80)
    feat_a = pd.DataFrame(np.column_stack([np.full(50, 100.0), np.zeros((50, 2))]))
    feat_b = pd.DataFrame(np.column_stack([np.full(80, 200.0), np.zeros((80, 2))]))

    train_lstm(fake, [df_a, df_b], [feat_a, feat_b])

    # Every window's label must still match the identity baked into its own
    # features - shuffling must never desync X from y.
    assert np.array_equal(fake.X_train[:, 0, 0], fake.y_train)
    # Both symbols must be represented in the shuffled training set.
    assert set(fake.y_train) == {100.0, 200.0}
    # It must actually be shuffled, not left in the concatenated
    # all-A-then-all-B order.
    n_a_train = int((50 - lookback) * 0.8)
    assert not np.array_equal(fake.y_train[:n_a_train], np.full(n_a_train, 100.0))


def test_train_xgboost_labels_each_symbol_independently():
    """prepare_labels() must be called once per symbol with that symbol's
    own row count, never once with a combined multi-symbol row count - its
    forward-looking lookahead window would otherwise read into the next
    symbol's prices near the join."""
    fake = _FakeXGB()
    df_a, df_b = _make_symbol_df(1.0, 50), _make_symbol_df(100.0, 80)
    feat_a = pd.DataFrame(np.zeros((50, 3)))
    feat_b = pd.DataFrame(np.zeros((80, 3)))

    metrics = train_xgboost(fake, [df_a, df_b], [feat_a, feat_b])

    assert fake.prepare_labels_calls == [50, 80]
    assert metrics == {'accuracy': 0.5, 'precision': 0.5}
    # One group id per symbol, so the CV split can walk forward in time
    # within each symbol independently instead of treating a later row in
    # the concatenated array as "the future" of an unrelated symbol.
    assert np.array_equal(fake.groups, np.concatenate([np.full(50, 0), np.full(80, 1)]))
    assert fake.trained_on_shape[0] == 130  # 50 + 80 rows combined
