import os
import logging
import numpy as np
from pathlib import Path
from dotenv import load_dotenv
import pandas as pd
import sys

# Ensure the project root is on the import path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data_ingestion import ForexDataPipeline
from src.features import FeatureEngine
from src.models.lstm_predictor import LSTMPredictor
from src.models.xgboost_classifier import XGBoostSignal

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------
logger = logging.getLogger('ModelTraining')
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

def get_historical_df(pipeline: ForexDataPipeline, symbol: str, timeframe: int) -> pd.DataFrame:
    """Fetch the full OHLCV history for a given symbol/timeframe from PostgreSQL."""
    df = pipeline.get_ohlcv(symbol, timeframe, limit=None)
    if df is None or df.empty:
        raise RuntimeError(f"No historical data found for {symbol} {timeframe}m")
    return df

def train_lstm(lstm: LSTMPredictor, df: pd.DataFrame, features_df: pd.DataFrame) -> None:
    """Prepare data and train the LSTM model."""
    (X_train, y_train), (X_test, y_test) = lstm.prepare_data(df, features_df, test_size=0.2)
    logger.info(f"LSTM training shapes - X: {X_train.shape}, y: {y_train.shape}")
    lstm.train(X_train, y_train, epochs=30, batch_size=32)
    loss, mae = lstm.model.evaluate(X_test, y_test, verbose=0)
    logger.info(f"LSTM validation - loss: {loss:.6f}, mae: {mae:.6f}")

def train_xgboost(xgb: XGBoostSignal, df: pd.DataFrame, features_df: pd.DataFrame) -> None:
    """Generate labels and train the XGBoost classifier.

    threshold_pct=0.002 (0.2%) labels more bars as directional vs the old 0.5%,
    fixing the class imbalance that caused XGBoost to always predict FLAT.
    """
    labels = xgb.prepare_labels(df, lookahead=5, threshold_pct=0.002)
    X = features_df.values
    logger.info(f"XGBoost training - {X.shape[0]} samples, {X.shape[1]} features")
    counts = {-1: (labels == -1).sum(), 0: (labels == 0).sum(), 1: (labels == 1).sum()}
    logger.info(f"Label distribution - SELL: {counts[-1]}, FLAT: {counts[0]}, BUY: {counts[1]}")
    metrics = xgb.train(X, labels, cv_folds=3, feature_names=list(features_df.columns))
    logger.info(f"XGBoost CV - accuracy: {metrics['accuracy']:.4f}, precision: {metrics['precision']:.4f}")

def main():
    load_dotenv()

    config = {
        'postgresql': {
            'dbname': os.getenv('FOREX_DB_NAME', 'forex_trading_db'),
            'user': os.getenv('FOREX_DB_USER', 'admin'),
            'password': os.getenv('FOREX_DB_PASSWORD', 'admin'),
            'host': os.getenv('FOREX_DB_HOST', 'localhost'),
            'port': os.getenv('FOREX_DB_PORT', '5432')
        },
        'symbols': []
    }

    from config.config import REDIS, MT5, ACTIVE_SYMBOLS, SYMBOLS
    config['symbols'] = ACTIVE_SYMBOLS
    pipeline = ForexDataPipeline(
        db_config=config['postgresql'],
        redis_config=REDIS,
        mt5_config=MT5
    )

    models_dir = Path(__file__).parent.parent / 'models'
    models_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # Collect all symbols' data into one combined dataset before training.
    # Previously the loop trained symbol-by-symbol and each iteration overwrote
    # the model, so only the last symbol's data was ever used.
    # -------------------------------------------------------------------------
    all_dfs = []
    all_features = []

    for symbol in ACTIVE_SYMBOLS:
        timeframes = SYMBOLS[symbol]['timeframes']
        tf = timeframes[0]  # use primary (4H) timeframe for training
        logger.info(f"Fetching historical data for {symbol} {tf}m")
        try:
            df = get_historical_df(pipeline, symbol, tf)
        except RuntimeError as e:
            logger.warning(str(e))
            continue

        engine = FeatureEngine(df)
        engine.add_technical_indicators() \
              .add_price_action_features() \
              .add_market_microstructure() \
              .normalize()
        features = engine.get_features(normalized=True)

        # Store original prices for XGBoost label generation, then normalise
        # close to pct_change so LSTM trains on a scale-free target across all
        # symbols (avoids scaler_y mismatch between e.g. EURUSD ~1.08 and GBPJPY ~184).
        df_pct = df.copy()
        df_pct['close'] = df['close'].pct_change().fillna(0)

        all_dfs.append(df_pct)       # pct_change close — for LSTM
        all_features.append((df, features))  # original df kept alongside features for XGB labels

    if not all_dfs:
        logger.error("No data collected — aborting training.")
        pipeline.close()
        return

    combined_df_pct = pd.concat(all_dfs, ignore_index=True)
    combined_df_orig = pd.concat([df for df, _ in all_features], ignore_index=True)
    combined_features = pd.concat([feat for _, feat in all_features], ignore_index=True)
    logger.info(
        f"Combined dataset: {len(combined_df_pct)} rows across {len(all_dfs)} symbols"
    )

    # -------------------------------------------------------------------------
    # Train models on the combined dataset
    # -------------------------------------------------------------------------
    lstm = LSTMPredictor(lookback=60)
    xgb_model = XGBoostSignal()

    logger.info("Training LSTM on combined dataset (pct_change close)...")
    train_lstm(lstm, combined_df_pct, combined_features)

    logger.info("Training XGBoost on combined dataset (original close for labels)...")
    train_xgboost(xgb_model, combined_df_orig, combined_features)

    lstm.save(str(models_dir))
    xgb_model.save(str(models_dir))
    logger.info(f"Models saved to {models_dir}")

    pipeline.close()

if __name__ == '__main__':
    main()
