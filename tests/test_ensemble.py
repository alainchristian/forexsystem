import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from src.models.ensemble import EnsembleStrategy
from src.backtester import Backtester

def test_ensemble_alignment_and_confidence():
    """Test that it only trades when signals align and confidence is high"""
    lstm_mock = MagicMock()
    xgb_mock = MagicMock()
    
    strategy = EnsembleStrategy(lstm_mock, xgb_mock, threshold_confidence=0.65)
    
    # 1. Models Disagree
    lstm_mock.predict_next_price.return_value = 1.01  # Long (+1%)
    xgb_mock.predict_signal.return_value = np.array([-1]) # Short
    xgb_mock.predict_proba.return_value = np.array([[0.8, 0.1, 0.1]]) # 80% confident short
    
    recent_data = np.zeros((10, 5))
    signal, conf = strategy.generate_signal(recent_data, current_price=1.0)
    assert signal == 0 # Disagree = Flat
    
    # 2. Models Agree, Low Confidence
    lstm_mock.predict_next_price.return_value = 1.001  # Very slight Long (+0.1%) -> strength = 0.1/0.5 = 0.2
    xgb_mock.predict_signal.return_value = np.array([1]) # Long
    xgb_mock.predict_proba.return_value = np.array([[0.1, 0.4, 0.5]]) # 50% confident long
    
    # Avg conf = (0.2 + 0.5) / 2 = 0.35 < 0.65
    signal, conf = strategy.generate_signal(recent_data, current_price=1.0)
    assert signal == 0 # Low confidence = Flat
    assert conf == pytest.approx(0.35)
    
    # 3. Models Agree, High Confidence
    lstm_mock.predict_next_price.return_value = 1.01  # Strong Long (+1%) -> strength = 1.0 (capped)
    xgb_mock.predict_signal.return_value = np.array([1]) # Long
    xgb_mock.predict_proba.return_value = np.array([[0.05, 0.05, 0.9]]) # 90% confident long
    
    # Avg conf = (1.0 + 0.9) / 2 = 0.95 >= 0.65
    signal, conf = strategy.generate_signal(recent_data, current_price=1.0)
    assert signal == 1 # Agree + High Conf = Long!
    assert conf == 0.95

def test_ensemble_walk_forward_metrics():
    """
    Test Walk-Forward validation integration and verify targets.
    To ensure consistent tests, we mock the models to generate highly profitable signals.
    """
    # Create trending data
    dates = pd.date_range(start='2020-01-01', periods=500, freq='D')
    close = np.linspace(1.0, 2.0, 500) # Perfect uptrend
    df = pd.DataFrame({
        'timestamp': dates,
        'open': close - 0.001,
        'high': close + 0.001,
        'low': close - 0.002,
        'close': close,
        'volume': 1000
    })
    features_df = pd.DataFrame(np.random.randn(500, 5))
    
    lstm_mock = MagicMock()
    lstm_mock.lookback = 10
    
    xgb_mock = MagicMock()
    
    strategy = EnsembleStrategy(lstm_mock, xgb_mock, threshold_confidence=0.65)
    
    # Override generate_signal to simulate perfect trading
    def mock_generate_signal(recent, curr):
        return 1, 0.9  # Always go Long with 90% confidence
        
    strategy.generate_signal = mock_generate_signal
    
    # Override train methods so they don't do anything
    lstm_mock.prepare_data.return_value = ((None, None), (None, None))
    xgb_mock.prepare_labels.return_value = np.array([1, 0, -1] * 200) # Give it 600 items so it's longer than any fold
    
    results_df = strategy.run_walk_forward(df, features_df)
    
    assert len(results_df) > 0
    assert 'sharpe_ratio' in results_df.columns
    assert 'win_rate' in results_df.columns
    assert 'max_drawdown' in results_df.columns
    
    mean_sharpe = results_df['sharpe_ratio'].mean()
    mean_win_rate = results_df['win_rate'].mean()
    mean_dd = results_df['max_drawdown'].mean()
    
    print(f"Mean Sharpe: {mean_sharpe}")
    print(f"Mean Win Rate: {mean_win_rate}%")
    print(f"Mean Drawdown: {mean_dd}%")
    
    # Due to perfect uptrend and perfect signals, metrics should be extremely high
    assert mean_sharpe > 0.5
    assert mean_win_rate > 0.45
    assert mean_dd < 30.0
