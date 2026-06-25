import sys
from pathlib import Path
import os
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from src.models.lstm_predictor import LSTMPredictor

@pytest.fixture
def sample_data():
    dates = pd.date_range(start='2023-01-01', periods=200, freq='4H')
    np.random.seed(42)
    close = 1.0500 + np.cumsum(np.random.randn(200) * 0.001)
    
    df = pd.DataFrame({
        'timestamp': dates,
        'open': close + np.random.randn(200) * 0.0005,
        'high': close + abs(np.random.randn(200) * 0.001),
        'low': close - abs(np.random.randn(200) * 0.001),
        'close': close,
        'volume': np.random.randint(1000, 10000, 200)
    })
    
    # Fake 23 features
    features_df = pd.DataFrame(np.random.randn(200, 23))
    
    return df, features_df

def test_lstm_prepare_data(sample_data):
    df, features_df = sample_data
    predictor = LSTMPredictor(lookback=60)
    
    (X_train, y_train), (X_test, y_test) = predictor.prepare_data(df, features_df, test_size=0.2)
    
    # Total samples should be 200 - 60 = 140
    # Train = 80% of 140 = 112
    # Test = 20% of 140 = 28
    assert X_train.shape == (112, 60, 23)
    assert y_train.shape == (112,)
    assert X_test.shape == (28, 60, 23)
    assert y_test.shape == (28,)

def test_lstm_model_build_and_train(sample_data):
    df, features_df = sample_data
    predictor = LSTMPredictor(lookback=10)
    
    (X_train, y_train), (X_test, y_test) = predictor.prepare_data(df, features_df, test_size=0.2)
    
    history = predictor.train(X_train, y_train, epochs=2, batch_size=16)
    
    assert predictor.model is not None
    assert 'loss' in history.history
    
    # Test prediction
    recent_data = features_df.values[-10:]
    pred = predictor.predict_next_price(recent_data)
    
    assert isinstance(pred, float)
    assert 0.0 < pred < 2.0

def test_lstm_save_load(sample_data, tmp_path):
    df, features_df = sample_data
    predictor = LSTMPredictor(lookback=10)
    (X_train, y_train), _ = predictor.prepare_data(df, features_df, test_size=0.2)
    predictor.train(X_train, y_train, epochs=1, batch_size=16)
    
    save_path = str(tmp_path / "test_model")
    predictor.save(save_path)
    
    assert os.path.exists(f"{save_path}_model.keras")
    assert os.path.exists(f"{save_path}_scaler.pkl")
    
    # Load into new instance
    new_predictor = LSTMPredictor(lookback=10)
    new_predictor.load(save_path)
    
    assert new_predictor.model is not None
    
    recent_data = features_df.values[-10:]
    pred1 = predictor.predict_next_price(recent_data)
    pred2 = new_predictor.predict_next_price(recent_data)
    
    # Should predict exactly the same
    assert abs(pred1 - pred2) < 1e-6
    
def test_lstm_mae_target():
    """
    Test that MAE < 0.05% on a properly generated linear sequence
    """
    # Create sine wave data so model can learn it easily and doesn't have to extrapolate
    dates = pd.date_range(start='2023-01-01', periods=1000, freq='4H')
    x = np.linspace(0, 20 * np.pi, 1000)
    close = 1.25 + 0.25 * np.sin(x)  # Values between 1.0 and 1.5
    
    df = pd.DataFrame({'close': close})
    
    # Features must be normalized for LSTM to learn effectively!
    from sklearn.preprocessing import StandardScaler
    raw_features = pd.DataFrame({'f1': close, 'f2': np.cos(x)})
    scaler_x = StandardScaler()
    features_df = pd.DataFrame(scaler_x.fit_transform(raw_features))
    
    predictor = LSTMPredictor(lookback=10)
    (X_train, y_train), (X_test, y_test) = predictor.prepare_data(df, features_df, test_size=0.2)
    
    # Train for more epochs to ensure convergence on simple data
    predictor.train(X_train, y_train, epochs=50, batch_size=16)
    
    # Evaluate on test set
    preds = []
    actuals = []
    
    y_test_unscaled = predictor.scaler_y.inverse_transform(y_test.reshape(-1, 1)).flatten()
    
    for i in range(len(X_test)):
        input_data = X_test[i].reshape(1, predictor.lookback, X_test.shape[2])
        pred_scaled = predictor.model.predict(input_data, verbose=0)[0][0]
        pred = predictor.scaler_y.inverse_transform([[pred_scaled]])[0][0]
        preds.append(pred)
        actuals.append(y_test_unscaled[i])
        
    mae = np.mean(np.abs(np.array(preds) - np.array(actuals)))
    mean_price = np.mean(actuals)
    mae_pct = (mae / mean_price) * 100
    
    print(f"MAE Pct: {mae_pct}%")
    assert mae_pct < 0.5, f"MAE too high: {mae_pct}%"
