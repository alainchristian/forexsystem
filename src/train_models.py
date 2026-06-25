import os
import logging
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
    """Fetch the full OHLCV history for a given symbol/timeframe from PostgreSQL.

    ``ForexDataPipeline.get_ohlcv`` returns a DataFrame ordered by timestamp
    ascending. Passing ``limit=None`` retrieves all rows.
    """
    df = pipeline.get_ohlcv(symbol, timeframe, limit=None)
    if df is None or df.empty:
        raise RuntimeError(f"No historical data found for {symbol} {timeframe}m")
    return df

def train_lstm(lstm: LSTMPredictor, df: pd.DataFrame, features_df: pd.DataFrame) -> None:
    """Prepare data and train the LSTM model.

    The ``prepare_data`` method splits the data (default 20 % test). We train on
    the training subset and log a quick validation loss.
    """
    (X_train, y_train), (X_test, y_test) = lstm.prepare_data(df, features_df, test_size=0.2)
    logger.info(f"LSTM training shapes – X: {X_train.shape}, y: {y_train.shape}")
    lstm.train(X_train, y_train, epochs=30, batch_size=32)
    loss, mae = lstm.model.evaluate(X_test, y_test, verbose=0)
    logger.info(f"LSTM validation – loss: {loss:.6f}, mae: {mae:.6f}")

def train_xgboost(xgb: XGBoostSignal, df: pd.DataFrame, features_df: pd.DataFrame) -> None:
    """Generate labels and train the XGBoost classifier.

    Labels are created with a 5‑bar look‑ahead and a 0.5 % movement threshold –
    the same parameters used during live inference.
    """
    labels = xgb.prepare_labels(df, lookahead=5, threshold_pct=0.005)
    X = features_df.values
    logger.info(f"XGBoost training – {X.shape[0]} samples, {X.shape[1]} features")
    metrics = xgb.train(X, labels, cv_folds=3, feature_names=list(features_df.columns))
    logger.info(f"XGBoost CV – accuracy: {metrics['accuracy']:.4f}, precision: {metrics['precision']:.4f}")

def main():
    # Load environment variables (database credentials, etc.)
    load_dotenv()

    # ---------------------------------------------------------------------
    # Configuration – mirrors the values used in src/main.py
    # ---------------------------------------------------------------------
    config = {
        'postgresql': {
            'dbname': os.getenv('FOREX_DB_NAME', 'forex_trading_db'),
            'user': os.getenv('FOREX_DB_USER', 'admin'),
            'password': os.getenv('FOREX_DB_PASSWORD', 'admin'),
            'host': os.getenv('FOREX_DB_HOST', 'localhost'),
            'port': os.getenv('FOREX_DB_PORT', '5432')
        },
        'symbols': ['EURUSDm', 'GBPUSDm', 'USDJPYm']
    }

    # Initialise the data pipeline (use full config defaults for Redis and MT5)
    from config.config import REDIS, MT5
    pipeline = ForexDataPipeline(
        db_config=config['postgresql'],
        redis_config=REDIS,
        mt5_config=MT5
    )

    # Ensure the models directory exists
    models_dir = Path(__file__).parent.parent / 'models'
    models_dir.mkdir(parents=True, exist_ok=True)

    lstm = LSTMPredictor(lookback=60)
    xgb = XGBoostSignal()

    # ---------------------------------------------------------------------
    # Train on each symbol / timeframe defined in config/config.py
    # ---------------------------------------------------------------------
    from config.config import SYMBOLS
    for symbol in config['symbols']:
        timeframes = SYMBOLS[symbol]['timeframes']
        for tf in timeframes:
            logger.info(f"Fetching historical data for {symbol} {tf}m")
            df = get_historical_df(pipeline, symbol, tf)
            engine = FeatureEngine(df)
            engine.add_technical_indicators() \
                  .add_price_action_features() \
                  .add_market_microstructure() \
                  .normalize()
            features = engine.get_features(normalized=True)
            train_lstm(lstm, df, features)
            train_xgboost(xgb, df, features)

    # ---------------------------------------------------------------------
    # Persist the trained models
    # ---------------------------------------------------------------------
    lstm.save(str(models_dir))
    xgb.save(str(models_dir))
    logger.info(f"Models saved to {models_dir}")

    pipeline.close()

if __name__ == '__main__':
    main()
