import numpy as np
import pandas as pd
import logging
import os
import pickle
import xgboost as xgb
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import accuracy_score, precision_score
from typing import Tuple, List, Dict

logger = logging.getLogger(__name__)

class XGBoostSignal:
    """
    XGBoost Signal Generator
    Generates ternary trading signals (-1, 0, 1) based on expected price movement.
    """
    def __init__(self):
        self.model = xgb.XGBClassifier(
            objective='multi:softprob', # softprob is better to get probabilities, softmax just gives class
            num_class=3,
            max_depth=6,
            learning_rate=0.1,
            n_estimators=200,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=-1
        )
        self.feature_names = None
        
    def prepare_labels(self, df: pd.DataFrame, lookahead: int = 5, threshold_pct: float = 0.005) -> np.ndarray:
        """
        Creates ternary labels for training.
        - 1 (LONG): close will rise > threshold_pct
        - 0 (FLAT): close stays within +/- threshold_pct
        - -1 (SHORT): close will fall > threshold_pct
        
        Args:
            df: OHLCV DataFrame
            lookahead: How many candles into the future to check
            threshold_pct: Percentage threshold for a move to be considered significant (e.g., 0.005 = 0.5%)
        """
        close = df['close'].values
        labels = np.zeros(len(close))
        
        # Calculate percentage change 'lookahead' steps into the future
        for i in range(len(close) - lookahead):
            future_price = close[i + lookahead]
            current_price = close[i]
            pct_change = (future_price - current_price) / current_price
            
            if pct_change > threshold_pct:
                labels[i] = 1
            elif pct_change < -threshold_pct:
                labels[i] = -1
            else:
                labels[i] = 0
                
        # The last 'lookahead' rows will have label 0 by default as we don't know the future
        logger.info(f"Generated labels: {-1}: {(labels == -1).sum()}, 0: {(labels == 0).sum()}, 1: {(labels == 1).sum()}")
        return labels

    def _map_labels_to_xgb(self, y: np.ndarray) -> np.ndarray:
        """XGBoost requires labels in range [0, num_classes-1]. Map -1,0,1 to 0,1,2"""
        # Map: -1 -> 0, 0 -> 1, 1 -> 2
        return y + 1
        
    def _map_xgb_to_labels(self, y: np.ndarray) -> np.ndarray:
        """Map XGBoost predictions 0,1,2 back to -1,0,1"""
        return y - 1
        
    def train(self, X: np.ndarray, y: np.ndarray, cv_folds: int = 5, feature_names: List[str] = None) -> Dict[str, float]:
        """
        Trains the XGBoost model using Stratified K-Fold cross-validation.
        
        Args:
            X: Feature matrix
            y: Labels (-1, 0, 1)
            cv_folds: Number of cross validation folds
            feature_names: Optional list of feature names for importance plotting
        """
        self.feature_names = feature_names if feature_names is not None else [f"feature_{i}" for i in range(X.shape[1])]
        y_xgb = self._map_labels_to_xgb(y)
        
        # shuffle=True so folds mix rows from every symbol in the combined
        # multi-symbol training set instead of splitting along whatever
        # order symbols happened to be concatenated in.
        skf = StratifiedKFold(n_splits=cv_folds, shuffle=True, random_state=42)
        
        cv_accuracies = []
        cv_precisions = []
        
        logger.info(f"Starting {cv_folds}-fold cross validation...")
        
        # Cross validation
        for fold, (train_idx, val_idx) in enumerate(skf.split(X, y_xgb)):
            X_train_cv, y_train_cv = X[train_idx], y_xgb[train_idx]
            X_val_cv, y_val_cv = X[val_idx], y_xgb[val_idx]
            
            # Use early stopping internally during CV
            clf = xgb.XGBClassifier(**self.model.get_params())
            clf.fit(
                X_train_cv, y_train_cv,
                eval_set=[(X_val_cv, y_val_cv)],
                verbose=False
            )
            
            preds = clf.predict(X_val_cv)
            acc = accuracy_score(y_val_cv, preds)
            prec = precision_score(y_val_cv, preds, average='macro', zero_division=0)
            
            cv_accuracies.append(acc)
            cv_precisions.append(prec)
            logger.info(f"Fold {fold+1}: Accuracy = {acc:.4f}, Precision = {prec:.4f}")
            
        mean_acc = np.mean(cv_accuracies)
        mean_prec = np.mean(cv_precisions)
        
        logger.info(f"CV Mean Accuracy: {mean_acc:.4f}, Mean Precision: {mean_prec:.4f}")
        
        # Train final model on ALL data
        logger.info("Training final model on full dataset...")
        self.model.fit(X, y_xgb)
        
        return {"accuracy": mean_acc, "precision": mean_prec}

    def predict_signal(self, X: np.ndarray) -> np.ndarray:
        """
        Predicts signals (-1, 0, 1) for the given features.
        """
        xgb_preds = self.model.predict(X)
        return self._map_xgb_to_labels(xgb_preds)
        
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """
        Returns prediction probabilities for classes [-1, 0, 1]
        Output shape: (n_samples, 3)
        """
        return self.model.predict_proba(X)
        
    def feature_importance(self) -> pd.DataFrame:
        """
        Returns a DataFrame of the top 10 most important features.
        """
        importance = self.model.feature_importances_
        df = pd.DataFrame({
            'Feature': self.feature_names,
            'Importance': importance
        })
        return df.sort_values(by='Importance', ascending=False).head(10).reset_index(drop=True)
        
    def save(self, path: str):
        """Saves model to disk"""
        os.makedirs(path, exist_ok=True)
        model_file = os.path.join(path, "xgboost_model.json")
        meta_file = os.path.join(path, "xgboost_meta.pkl")
        
        self.model.save_model(model_file)
        with open(meta_file, "wb") as f:
            pickle.dump({'feature_names': self.feature_names}, f)
        logger.info(f"Saved XGBoost to {model_file}")
            
    def load(self, path: str):
        """Loads model from disk"""
        model_file = os.path.join(path, "xgboost_model.json")
        meta_file = os.path.join(path, "xgboost_meta.pkl")
        
        self.model.load_model(model_file)
        with open(meta_file, "rb") as f:
            meta = pickle.load(f)
            self.feature_names = meta['feature_names']
        logger.info(f"Loaded XGBoost from {model_file}")
