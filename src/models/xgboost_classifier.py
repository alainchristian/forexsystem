import numpy as np
import pandas as pd
import logging
import os
import pickle
import xgboost as xgb
from sklearn.metrics import accuracy_score, precision_score
from typing import Tuple, List, Dict, Optional

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

    @staticmethod
    def _purged_group_splits(groups: np.ndarray, n_splits: int, embargo: int):
        """
        Forward-chaining, embargoed time-series split, computed independently
        within each group and then combined per fold index.

        Rows are assumed to be in chronological order *within* each group,
        but rows are typically concatenated group-by-group (e.g. one block
        per symbol) - so a later row in the array is not necessarily a later
        timestamp overall, just a different group covering roughly the same
        calendar range. Splitting globally on array order would validate on
        one symbol while training on another symbol from the same period,
        which isn't a real time split at all. Splitting per group and then
        combining fold i across every group keeps each fold's training data
        strictly earlier than its validation data, for every group.

        `embargo` rows immediately before each validation cut are dropped
        from training. Labels are built by prepare_labels() looking
        `lookahead` bars into the future, so a training row within
        `lookahead` bars of the validation cut has a label window that
        overlaps the validation set - purging it prevents that leakage.
        Callers should pass embargo >= the lookahead used to build labels.
        """
        unique_groups = np.unique(groups)
        fold_train_idx: List[List[int]] = [[] for _ in range(n_splits)]
        fold_val_idx: List[List[int]] = [[] for _ in range(n_splits)]

        for g in unique_groups:
            g_idx = np.flatnonzero(groups == g)
            n = len(g_idx)
            fold_size = n // (n_splits + 1)
            if fold_size == 0:
                continue
            for i in range(n_splits):
                train_end = fold_size * (i + 1)
                val_start = train_end
                val_end = min(val_start + fold_size, n)
                if val_end <= val_start:
                    continue
                purge_start = max(0, train_end - embargo)
                fold_train_idx[i].extend(g_idx[:purge_start])
                fold_val_idx[i].extend(g_idx[val_start:val_end])

        for i in range(n_splits):
            if not fold_train_idx[i] or not fold_val_idx[i]:
                continue
            yield np.array(fold_train_idx[i]), np.array(fold_val_idx[i])

    def train(self, X: np.ndarray, y: np.ndarray, cv_folds: int = 5,
              feature_names: List[str] = None, groups: Optional[np.ndarray] = None,
              embargo: int = 5) -> Dict[str, float]:
        """
        Trains the XGBoost model, reporting CV metrics from a purged,
        embargoed, forward-chaining time-series split (not a shuffled
        Stratified K-Fold - shuffling would put validation rows next to
        training rows whose lookahead label window overlaps them, since
        prepare_labels() labels row i from close[i+lookahead], which leaks
        and inflates the reported accuracy).

        Args:
            X: Feature matrix
            y: Labels (-1, 0, 1)
            cv_folds: Number of cross validation folds
            feature_names: Optional list of feature names for importance plotting
            groups: Optional per-row group id (e.g. one id per symbol) so the
                split walks forward in time independently within each group
                before combining fold-by-fold. Required whenever X/y are the
                concatenation of multiple symbols/series - without it, a
                later row in the array is treated as "the future" even
                though it may just be a different symbol's data from the
                same calendar period. Defaults to a single group (X/y is one
                contiguous time series).
            embargo: Rows purged from training immediately before each
                validation cut. Must be >= the `lookahead` passed to
                prepare_labels() for the labels in `y`, or the lookahead
                window will still leak across the boundary.
        """
        self.feature_names = feature_names if feature_names is not None else [f"feature_{i}" for i in range(X.shape[1])]
        y_xgb = self._map_labels_to_xgb(y)

        if groups is None:
            groups = np.zeros(len(y_xgb), dtype=int)

        cv_accuracies = []
        cv_precisions = []

        logger.info(f"Starting {cv_folds}-fold purged walk-forward validation (embargo={embargo})...")

        for fold, (train_idx, val_idx) in enumerate(self._purged_group_splits(groups, cv_folds, embargo)):
            X_train_cv, y_train_cv = X[train_idx], y_xgb[train_idx]
            X_val_cv, y_val_cv = X[val_idx], y_xgb[val_idx]

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
            logger.info(
                f"Fold {fold+1}: Accuracy = {acc:.4f}, Precision = {prec:.4f} "
                f"(train={len(train_idx)}, val={len(val_idx)})"
            )

        mean_acc = float(np.mean(cv_accuracies)) if cv_accuracies else float('nan')
        mean_prec = float(np.mean(cv_precisions)) if cv_precisions else float('nan')

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
