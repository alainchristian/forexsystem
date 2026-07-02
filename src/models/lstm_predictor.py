import numpy as np
import pandas as pd
import logging
import os
import pickle
import tensorflow as tf
from tensorflow.keras.models import Sequential, load_model
from tensorflow.keras.layers import LSTM, Dense, Dropout
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.callbacks import EarlyStopping
from sklearn.preprocessing import StandardScaler
from typing import Tuple

logger = logging.getLogger(__name__)

class LSTMPredictor:
    """
    LSTM Price Predictor
    Predicts the next candle close price using a lookback window of normalized features.
    """
    def __init__(self, lookback: int = 60):
        self.lookback = lookback
        self.model = None
        self.scaler_y = StandardScaler()
        
    def prepare_data(self, df: pd.DataFrame, features_df: pd.DataFrame, test_size: float = 0.2) -> Tuple[Tuple[np.ndarray, np.ndarray], Tuple[np.ndarray, np.ndarray]]:
        """
        Prepares the data for LSTM training.
        
        Args:
            df: OHLCV DataFrame
            features_df: Normalized features DataFrame
            test_size: Proportion of data to use for testing
            
        Returns:
            ((X_train, y_train), (X_test, y_test))
        """
        features = features_df.values
        targets = df['close'].values
        
        # Scale targets to help LSTM converge
        targets_scaled = self.scaler_y.fit_transform(targets.reshape(-1, 1)).flatten()
        
        X, y = [], []
        # Create sequences
        # If we use features[i - lookback : i], that's 'lookback' candles ending at i-1.
        # We want to predict the close of candle i.
        for i in range(self.lookback, len(features)):
            X.append(features[i - self.lookback:i])
            y.append(targets_scaled[i])
            
        X = np.array(X)
        y = np.array(y)
        
        split_idx = int(len(X) * (1 - test_size))
        
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]
        
        logger.info(f"Prepared data: X_train shape {X_train.shape}, y_train shape {y_train.shape}")
        
        return (X_train, y_train), (X_test, y_test)
        
    def build_model(self, input_shape: Tuple[int, int]) -> tf.keras.Model:
        """
        Builds the LSTM neural network architecture.
        """
        model = Sequential([
            LSTM(128, return_sequences=True, input_shape=input_shape),
            Dropout(0.2),
            LSTM(64, return_sequences=False),
            Dropout(0.2),
            Dense(32, activation='relu'),
            Dense(1)
        ])
        
        model.compile(optimizer=Adam(learning_rate=0.001), loss='mse', metrics=['mae'])
        self.model = model
        logger.info(f"Built LSTM model with input shape {input_shape}")
        return model
        
    def train(self, X_train: np.ndarray, y_train: np.ndarray, epochs: int = 50, batch_size: int = 32):
        """
        Trains the LSTM model with early stopping.
        """
        if self.model is None:
            self.build_model(input_shape=(X_train.shape[1], X_train.shape[2]))
            
        early_stopping = EarlyStopping(
            monitor='val_loss', 
            patience=5, 
            restore_best_weights=True
        )
        
        logger.info(f"Training LSTM for {epochs} epochs...")
        history = self.model.fit(
            X_train, y_train,
            epochs=epochs,
            batch_size=batch_size,
            validation_split=0.1,
            callbacks=[early_stopping],
            verbose=1
        )
        return history
        
    def predict_next_price(self, recent_data: np.ndarray) -> float:
        """
        Predicts the close price of the next candle based on recent data.
        
        Args:
            recent_data: NumPy array of shape (lookback, num_features)
            
        Returns:
            Predicted price (float)
        """
        if self.model is None:
            raise ValueError("Model is not trained or loaded yet.")
            
        if len(recent_data) != self.lookback:
            raise ValueError(f"recent_data must have exactly {self.lookback} rows.")
            
        # Reshape to (batch=1, timesteps, features)
        input_data = recent_data.reshape(1, self.lookback, recent_data.shape[1])
        pred_scaled = self.model.predict(input_data, verbose=0)[0][0]
        
        pred = self.scaler_y.inverse_transform([[pred_scaled]])[0][0]
        return float(pred)
        
    def save(self, path: str):
        """Saves the model weights and target scaler to disk."""
        os.makedirs(path, exist_ok=True)
        model_file = os.path.join(path, "lstm_model.keras")
        scaler_file = os.path.join(path, "lstm_scaler.pkl")
        
        self.model.save(model_file)
        with open(scaler_file, "wb") as f:
            pickle.dump(self.scaler_y, f)
        logger.info(f"Saved model to {model_file}")
            
    def load(self, path: str):
        """Loads the model weights and target scaler from disk."""
        model_file = os.path.join(path, "lstm_model.keras")
        scaler_file = os.path.join(path, "lstm_scaler.pkl")
        
        self.model = load_model(model_file)
        with open(scaler_file, "rb") as f:
            self.scaler_y = pickle.load(f)
        logger.info(f"Loaded model from {model_file}")
