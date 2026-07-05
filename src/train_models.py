import os
import logging
import subprocess
import numpy as np
from datetime import datetime
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
from src import model_versioning

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

def train_lstm(lstm: LSTMPredictor, per_symbol_dfs: list, per_symbol_features: list) -> dict:
    """Prepare data per-symbol, then combine, so no lookback window or
    train/test split ever crosses a symbol boundary.

    prepare_data() windows features[i-lookback:i] to predict row i. Calling
    it once on all symbols concatenated end-to-end let windows near each
    of the ~23 symbol boundaries splice together candles from two
    different currency pairs, and the trailing 20% test split held out
    whichever symbols happened to land last in the concatenation instead
    of a representative slice of every symbol.
    """
    X_train_parts, y_train_parts, X_test_parts, y_test_parts = [], [], [], []
    for df, features_df in zip(per_symbol_dfs, per_symbol_features):
        (X_tr, y_tr), (X_te, y_te) = lstm.prepare_data(df, features_df, test_size=0.2)
        X_train_parts.append(X_tr)
        y_train_parts.append(y_tr)
        X_test_parts.append(X_te)
        y_test_parts.append(y_te)

    X_train = np.concatenate(X_train_parts)
    y_train = np.concatenate(y_train_parts)
    X_test = np.concatenate(X_test_parts)
    y_test = np.concatenate(y_test_parts)

    # Shuffle the combined training set once (X/y paired) before handing it
    # to Keras. model.fit()'s internal validation_split takes the last 10%
    # of whatever order the array is already in, before any shuffling - left
    # unshuffled, that slice would still be biased toward whichever symbols
    # happen to land last in this concatenation, the same root issue as the
    # windowing/labeling bug, just one level deeper (affects early-stopping
    # decisions during training, not the final held-out test metrics above).
    rng = np.random.default_rng(42)
    perm = rng.permutation(len(X_train))
    X_train, y_train = X_train[perm], y_train[perm]

    logger.info(f"LSTM training shapes - X: {X_train.shape}, y: {y_train.shape}")
    lstm.train(X_train, y_train, epochs=30, batch_size=32)
    loss, mae = lstm.model.evaluate(X_test, y_test, verbose=0)
    logger.info(f"LSTM validation - loss: {loss:.6f}, mae: {mae:.6f}")
    return {"val_loss": float(loss), "val_mae": float(mae)}

def train_xgboost(xgb: XGBoostSignal, per_symbol_dfs: list, per_symbol_features: list) -> dict:
    """Generate labels per-symbol, then combine, and train the classifier.

    prepare_labels() looks `lookahead` bars into the future (close[i+lookahead])
    to label row i - calling it on the symbol-concatenated dataframe let that
    lookahead read into the next symbol's prices near each boundary.

    threshold_pct=0.002 (0.2%) labels more bars as directional vs the old 0.5%,
    fixing the class imbalance that caused XGBoost to always predict FLAT.
    """
    label_parts = [xgb.prepare_labels(df, lookahead=5, threshold_pct=0.002) for df in per_symbol_dfs]
    labels = np.concatenate(label_parts)
    features_df = pd.concat(per_symbol_features, ignore_index=True)
    X = features_df.values

    logger.info(f"XGBoost training - {X.shape[0]} samples, {X.shape[1]} features")
    counts = {-1: (labels == -1).sum(), 0: (labels == 0).sum(), 1: (labels == 1).sum()}
    logger.info(f"Label distribution - SELL: {counts[-1]}, FLAT: {counts[0]}, BUY: {counts[1]}")
    metrics = xgb.train(X, labels, cv_folds=3, feature_names=list(features_df.columns))
    logger.info(f"XGBoost CV - accuracy: {metrics['accuracy']:.4f}, precision: {metrics['precision']:.4f}")
    return metrics

def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent.parent, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"

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

    # Capture whatever's currently live as a rollback point before touching
    # anything, in case this models/ directory predates versioning.
    model_versioning.bootstrap_baseline(models_dir)

    # -------------------------------------------------------------------------
    # Collect all symbols' data into one combined dataset before training.
    # Previously the loop trained symbol-by-symbol and each iteration overwrote
    # the model, so only the last symbol's data was ever used.
    # -------------------------------------------------------------------------
    all_dfs = []
    all_features = []
    trained_symbols = []

    for symbol in ACTIVE_SYMBOLS:
        timeframes = SYMBOLS[symbol]['timeframes']
        tf = timeframes[0]  # use primary (4H) timeframe for training
        logger.info(f"Fetching historical data for {symbol} {tf}m")
        try:
            df = get_historical_df(pipeline, symbol, tf)
        except RuntimeError as e:
            logger.warning(str(e))
            continue
        trained_symbols.append(symbol)

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

    total_rows = sum(len(df) for df in all_dfs)
    logger.info(f"Combined dataset: {total_rows} rows across {len(all_dfs)} symbols")

    # -------------------------------------------------------------------------
    # Train models — windowed/labeled per-symbol inside train_lstm/train_xgboost,
    # only combined afterward, so no lookback window or lookahead label ever
    # crosses a symbol boundary.
    # -------------------------------------------------------------------------
    lstm = LSTMPredictor(lookback=60)
    xgb_model = XGBoostSignal()
    per_symbol_features = [feat for _, feat in all_features]

    logger.info("Training LSTM (pct_change close)...")
    lstm_metrics = train_lstm(lstm, all_dfs, per_symbol_features)

    logger.info("Training XGBoost (original close for labels)...")
    xgb_metrics = train_xgboost(xgb_model, [df for df, _ in all_features], per_symbol_features)

    # -------------------------------------------------------------------------
    # Save into a new timestamped version, then promote it to the fixed
    # models/ path main.py loads from. Keeping every version (pruned to the
    # most recent 8) means a bad training run can be rolled back instead of
    # silently overwriting the model that was working.
    # -------------------------------------------------------------------------
    version_dir = model_versioning.new_version_dir(models_dir)
    lstm.save(str(version_dir))
    xgb_model.save(str(version_dir))

    metadata = {
        "trained_at": datetime.now().isoformat(),
        "symbols": trained_symbols,
        "rows": total_rows,
        "lstm": lstm_metrics,
        "xgboost": xgb_metrics,
        "git_commit": _git_commit(),
    }
    model_versioning.record_version(models_dir, version_dir, metadata)
    model_versioning.promote(models_dir, version_dir.name)
    model_versioning.prune(models_dir, keep=8)
    logger.info(f"Model version {version_dir.name} trained, promoted to live, and saved to {models_dir}")

    pipeline.close()

def _print_versions(models_dir: Path) -> None:
    versions = model_versioning.list_versions(models_dir)
    if not versions:
        print("No model versions recorded yet.")
        return
    active = model_versioning.active_version(models_dir)
    for v in versions:
        marker = " (active)" if v["id"] == active else ""
        lstm_mae = v.get("lstm", {}).get("val_mae")
        xgb_acc = v.get("xgboost", {}).get("accuracy")
        print(
            f"{v['id']}{marker} — rows={v.get('rows', '?')} "
            f"lstm_val_mae={lstm_mae} xgboost_accuracy={xgb_acc} "
            f"git={v.get('git_commit', '?')}"
        )

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description="Train, list, or roll back the LSTM/XGBoost model ensemble")
    parser.add_argument('--list-versions', action='store_true', help="List saved model versions and exit")
    parser.add_argument(
        '--rollback', nargs='?', const='__previous__', default=None, metavar='VERSION_ID',
        help="Make VERSION_ID the live model (or the previous version if omitted) and exit, without retraining"
    )
    args = parser.parse_args()

    models_dir = Path(__file__).parent.parent / 'models'

    if args.list_versions:
        _print_versions(models_dir)
    elif args.rollback is not None:
        target = None if args.rollback == '__previous__' else args.rollback
        try:
            restored = model_versioning.rollback(models_dir, target)
        except (RuntimeError, ValueError) as e:
            print(f"Rollback failed: {e}")
            sys.exit(1)
        print(f"Rolled back - live models/ is now version {restored}")
    else:
        main()
