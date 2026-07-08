import numpy as np
import pandas as pd
import logging
from typing import Tuple, List, Dict
import datetime

from sklearn.preprocessing import StandardScaler

# Import Phase 1 Backtester
from src.backtester import Backtester
from src.models.lstm_predictor import LSTMPredictor
from src.models.xgboost_classifier import XGBoostSignal
from config.config import ENSEMBLE_CONFIDENCE_THRESHOLD

logger = logging.getLogger(__name__)

class EnsembleStrategy:
    """
    Ensemble Strategy combines LSTM and XGBoost models to generate trading signals.
    """
    def __init__(self, lstm_predictor: LSTMPredictor, xgboost_signal: XGBoostSignal,
                 threshold_confidence: float = ENSEMBLE_CONFIDENCE_THRESHOLD):
        self.lstm = lstm_predictor
        self.xgb = xgboost_signal
        self.threshold = threshold_confidence
        
    def generate_signal(self, recent_data: np.ndarray, current_price: float, symbol: str = "") -> Tuple[int, float]:
        """
        Generates an ensemble signal.
        Args:
            recent_data: NumPy array of shape (lookback, num_features) for LSTM.
                         The last row is used for XGBoost.
            current_price: Current close price to compare against LSTM prediction.
            symbol: Trading pair, included in log messages so a symbol can be
                    identified without cross-referencing the caller's own log
                    line. Optional (defaults to no prefix) since backtest/
                    walk-forward callers don't track a symbol per call.

        Returns:
            (signal, confidence):
            signal is 1 (Long), -1 (Short), or 0 (Flat)
            confidence is [0.0, 1.0]
        """
        prefix = f"{symbol}: " if symbol else ""
        # 1. LSTM Signal
        try:
            # train_models.py trains this model on pct_change targets, not
            # raw price, so one shared LSTM generalizes across symbols of
            # very different price scales (EURUSD ~1.08 vs GBPJPY ~184).
            # predict_next_price() therefore returns a predicted pct_change
            # here, not an absolute price. Diffing it against current_price
            # (as if it were a price) is what previously made every symbol
            # saturate to SELL at strength=1.0 regardless of the actual
            # prediction, since a ~0.001-scale value is negligible next to
            # a ~1+-scale price.
            lstm_pred_pct_change = self.lstm.predict_next_price(recent_data)

            if lstm_pred_pct_change > 0:
                lstm_signal = 1
            elif lstm_pred_pct_change < 0:
                lstm_signal = -1
            else:
                lstm_signal = 0

            # Cap strength at 1.0 based on a 0.5% move expectation
            lstm_strength = min(abs(lstm_pred_pct_change) / 0.005, 1.0)
        except Exception as e:
            logger.warning(f"{prefix}LSTM prediction failed: {e}")
            lstm_signal = 0
            lstm_strength = 0.0

        # 2. XGBoost Signal
        try:
            xgb_features = recent_data[-1].reshape(1, -1)
            xgb_signal = int(self.xgb.predict_signal(xgb_features)[0])
            probas = self.xgb.predict_proba(xgb_features)[0]
            # Probabilities map to indices: 0 -> Short (-1), 1 -> Flat (0), 2 -> Long (1)
            if xgb_signal == -1:
                xgb_conf = probas[0]
            elif xgb_signal == 1:
                xgb_conf = probas[2]
            else:
                xgb_conf = probas[1]
        except Exception as e:
            logger.warning(f"{prefix}XGBoost prediction failed: {e}")
            xgb_signal = 0
            xgb_conf = 0.0

        # 3. Log both models for visibility (LSTM is informational only)
        lstm_dir = {1: "BUY", -1: "SELL", 0: "FLAT"}[lstm_signal]
        xgb_dir  = {1: "BUY", -1: "SELL", 0: "FLAT"}[xgb_signal]
        logger.info(
            f"{prefix}LSTM: {lstm_dir} (strength={lstm_strength:.3f})  "
            f"XGB: {xgb_dir} (conf={xgb_conf:.3f})  threshold={self.threshold:.2f}"
        )

        # 4. Signal driven by XGBoost alone. LSTM's pct_change prediction is
        #    now computed correctly (see the scaler-mismatch fix above) and
        #    logged for visibility, but it still doesn't gate the trade -
        #    incorporating it into the actual decision is a separate call
        #    to make once its live signal quality has been validated.
        #    Fire when XGBoost picks a directional class above the threshold.
        if xgb_signal == 0:
            logger.info(f"{prefix}No signal: XGBoost returned FLAT")
            return 0, xgb_conf

        if xgb_conf < self.threshold:
            logger.info(
                f"{prefix}XGB {xgb_dir} signal blocked by confidence filter: "
                f"{xgb_conf:.3f} < {self.threshold:.2f}"
            )
            return 0, xgb_conf

        lstm_note = f"LSTM={lstm_dir}" if lstm_signal == xgb_signal else f"LSTM={lstm_dir} (disagrees)"
        logger.info(f"{prefix}XGB {xgb_dir} signal confirmed at {xgb_conf:.3f} | {lstm_note}")
        return xgb_signal, xgb_conf
        
    def backtest_ensemble(self, df: pd.DataFrame, features_df: pd.DataFrame) -> Dict:
        """
        Generates historical signals using the ensemble and runs them through the backtester.
        """
        logger.info("Generating historical signals for backtesting...")
        signals = np.zeros(len(df))
        
        # We need a rolling window of 'lookback' size for the LSTM
        lookback = self.lstm.lookback
        features = features_df.values
        closes = df['close'].values
        
        for i in range(lookback, len(df)):
            recent_data = features[i - lookback:i]
            current_price = closes[i-1]
            
            signal, conf = self.generate_signal(recent_data, current_price)
            signals[i] = signal
            
        logger.info(f"Generated {np.sum(signals != 0)} actionable signals out of {len(df)} candles.")
        
        # Run Backtest
        backtester = Backtester(df, initial_capital=10000.0)
        backtester.backtest(df, signals)
        return backtester.report()

    def run_walk_forward(self, df: pd.DataFrame, features_df: pd.DataFrame) -> pd.DataFrame:
        """
        Runs Walk-Forward validation (Train 252 periods, Test 63 periods).
        Assumes daily or 4H data. For simplicity, treats periods as indices.
        """
        train_window = 252
        test_window = 63
        
        total_len = len(df)
        if total_len < train_window + test_window:
            raise ValueError("Data too short for even one walk-forward fold.")
            
        start_idx = 0
        fold = 1
        all_results = []
        
        while start_idx + train_window + test_window <= total_len:
            logger.info(f"--- Walk-Forward Fold {fold} ---")
            train_end = start_idx + train_window
            test_end = train_end + test_window
            
            # 1. Split Data
            df_train = df.iloc[start_idx:train_end].copy()
            feat_train_raw = features_df.iloc[start_idx:train_end].copy()

            df_test = df.iloc[train_end:test_end].copy()

            # Fit scaler on training fold only — prevents data leakage into test fold
            fold_scaler = StandardScaler()
            feat_train = pd.DataFrame(
                fold_scaler.fit_transform(feat_train_raw.values),
                columns=feat_train_raw.columns,
                index=feat_train_raw.index,
            )

            # 2. Train LSTM
            logger.info("Training LSTM...")
            (X_tr, y_tr), _ = self.lstm.prepare_data(df_train, feat_train, test_size=0.0) # Train on all
            self.lstm.train(X_tr, y_tr, epochs=10, batch_size=16) # Fast train for WF

            # 3. Train XGBoost
            logger.info("Training XGBoost...")
            xgb_labels = self.xgb.prepare_labels(df_train, lookahead=5, threshold_pct=0.005)
            # Drop last 5
            X_xgb = feat_train.values[:-5]
            y_xgb = xgb_labels[:-5]
            # Ensure all classes exist to prevent xgb crashing, if not skip fold
            if len(np.unique(y_xgb)) < 3:
                logger.warning("Not all classes present in XGBoost train fold, skipping fold.")
                start_idx += test_window
                continue
                
            self.xgb.train(X_xgb, y_xgb, cv_folds=2) # Fast CV for WF
            
            # 4. Test on out-of-sample data
            logger.info("Evaluating on Test set...")
            
            # Prepend lookback candles from train so LSTM has context for first test candle.
            # Apply the same scaler (fitted on train) to this extended window — no leakage.
            lookback = self.lstm.lookback
            test_df_extended = df.iloc[train_end - lookback : test_end].reset_index(drop=True)
            feat_ext_raw = features_df.iloc[train_end - lookback : test_end]
            test_feat_extended = pd.DataFrame(
                fold_scaler.transform(feat_ext_raw.values),
                columns=feat_ext_raw.columns,
                index=feat_ext_raw.index,
            ).reset_index(drop=True)
            
            res = self.backtest_ensemble(test_df_extended, test_feat_extended)
            res['fold'] = fold
            res['test_start'] = df_test.index[0] if isinstance(df_test.index[0], datetime.date) else train_end
            
            all_results.append(res)
            
            start_idx += test_window
            fold += 1
            
        return pd.DataFrame(all_results)
