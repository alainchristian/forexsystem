import sys
from pathlib import Path
import os
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from src.models.xgboost_classifier import XGBoostSignal

@pytest.fixture
def sample_data():
    np.random.seed(42)
    # Generate 1000 candles
    close = 1.0500 + np.cumsum(np.random.randn(1000) * 0.002)
    df = pd.DataFrame({'close': close})
    
    # Generate 23 features that are predictive of the labels to ensure high accuracy
    features = np.random.randn(1000, 23)
    
    # Force some predictive power so CV accuracy > 55%
    future_returns = np.zeros(1000)
    for i in range(1000 - 5):
        future_returns[i] = (close[i+5] - close[i]) / close[i]
        
    # Make feature_0 heavily correlated with future returns
    features[:, 0] = future_returns + np.random.randn(1000) * 0.001
    
    features_df = pd.DataFrame(features, columns=[f"feat_{i}" for i in range(23)])
    
    return df, features_df

def test_xgboost_prepare_labels(sample_data):
    df, _ = sample_data
    model = XGBoostSignal()
    labels = model.prepare_labels(df, lookahead=5, threshold_pct=0.005)
    
    assert len(labels) == len(df)
    unique_labels = np.unique(labels)
    assert set(unique_labels).issubset({-1.0, 0.0, 1.0})

def test_xgboost_train_and_predict(sample_data):
    df, features_df = sample_data
    model = XGBoostSignal()
    
    # Use smaller threshold to ensure balanced classes for test data
    labels = model.prepare_labels(df, lookahead=5, threshold_pct=0.002)
    
    # Drop last 5 rows since they don't have future labels
    X = features_df.values[:-5]
    y = labels[:-5]
    
    # Ensure all classes are present for stratified split
    assert len(np.unique(y)) == 3, "Test data does not contain all 3 classes"
    
    results = model.train(X, y, cv_folds=5, feature_names=features_df.columns.tolist())
    
    assert 'accuracy' in results
    assert 'precision' in results
    print(f"Accuracy: {results['accuracy']}")
    assert results['accuracy'] > 0.55  # Target > 55%
    
    # Predict
    preds = model.predict_signal(X[:10])
    assert preds.shape == (10,)
    assert set(np.unique(preds)).issubset({-1, 0, 1})
    
    # Proba
    proba = model.predict_proba(X[:10])
    assert proba.shape == (10, 3)
    
def test_xgboost_feature_importance(sample_data):
    df, features_df = sample_data
    model = XGBoostSignal()
    labels = model.prepare_labels(df, lookahead=5, threshold_pct=0.002)
    
    X = features_df.values[:-5]
    y = labels[:-5]
    
    model.train(X, y, cv_folds=3, feature_names=features_df.columns.tolist())
    
    importance_df = model.feature_importance()
    assert len(importance_df) <= 10
    assert 'Feature' in importance_df.columns
    assert 'Importance' in importance_df.columns
    # Our mocked data made feat_0 highly correlated
    assert importance_df.iloc[0]['Feature'] == 'feat_0'

def test_xgboost_save_load(sample_data, tmp_path):
    df, features_df = sample_data
    model = XGBoostSignal()
    labels = model.prepare_labels(df, lookahead=5, threshold_pct=0.002)
    X = features_df.values[:-5]
    y = labels[:-5]
    
    model.train(X, y, cv_folds=3)
    
    save_path = str(tmp_path / "test_xgb")
    model.save(save_path)

    # save()/load() treat save_path as a directory, not a filename prefix
    assert os.path.exists(os.path.join(save_path, "xgboost_model.json"))
    
    new_model = XGBoostSignal()
    new_model.load(save_path)
    
    preds1 = model.predict_signal(X[:5])
    preds2 = new_model.predict_signal(X[:5])
    
    np.testing.assert_array_equal(preds1, preds2)
