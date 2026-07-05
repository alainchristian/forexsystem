"""
Feature Engineering Module - Phase 1
Modular calculation of technical indicators and price-action features
"""

import numpy as np
import pandas as pd
import logging
import pickle
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from sklearn.preprocessing import StandardScaler
import traceback

from config.config import FEATURES, LOGS_DIR

logger = logging.getLogger(__name__)

# ============================================================================
# TECHNICAL INDICATOR FUNCTIONS
# ============================================================================

class TechnicalIndicators:
    """Collection of technical indicator calculations"""
    
    @staticmethod
    def sma(series: pd.Series, period: int) -> pd.Series:
        """Simple Moving Average"""
        return series.rolling(window=period, min_periods=period).mean()
    
    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        """Exponential Moving Average"""
        return series.ewm(span=period, adjust=False).mean()
    
    @staticmethod
    def rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """Relative Strength Index (0-100)"""
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period, min_periods=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period, min_periods=period).mean()
        
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    @staticmethod
    def macd(series: pd.Series, 
            fast: int = 12, 
            slow: int = 26, 
            signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """MACD (Moving Average Convergence Divergence)"""
        ema_fast = series.ewm(span=fast, adjust=False).mean()
        ema_slow = series.ewm(span=slow, adjust=False).mean()
        
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        
        return macd_line, signal_line, histogram
    
    @staticmethod
    def bollinger_bands(series: pd.Series, 
                       period: int = 20, 
                       num_std: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """Bollinger Bands (Upper, Middle, Lower)"""
        sma = series.rolling(window=period, min_periods=period).mean()
        std = series.rolling(window=period, min_periods=period).std()
        
        upper = sma + (std * num_std)
        lower = sma - (std * num_std)
        
        return upper, sma, lower
    
    @staticmethod
    def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """Average True Range"""
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period, min_periods=period).mean()
        
        return atr
    
    @staticmethod
    def stochastic(high: pd.Series, low: pd.Series, close: pd.Series, 
                  period: int = 14, smooth_k: int = 3) -> Tuple[pd.Series, pd.Series]:
        """Stochastic Oscillator (%K, %D)"""
        lowest_low = low.rolling(window=period, min_periods=period).min()
        highest_high = high.rolling(window=period, min_periods=period).max()
        
        k_percent = 100 * ((close - lowest_low) / (highest_high - lowest_low + 1e-10))
        k_percent = k_percent.rolling(window=smooth_k, min_periods=smooth_k).mean()
        d_percent = k_percent.rolling(window=3, min_periods=3).mean()
        
        return k_percent, d_percent
    
    @staticmethod
    def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """Average Directional Index (trend strength, 0-100)"""
        plus_dm = high.diff()
        minus_dm = -low.diff()
        
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        
        tr = TechnicalIndicators.atr(high, low, close, period=1)
        
        plus_di = 100 * (plus_dm.rolling(window=period, min_periods=period).mean() / 
                        (tr.rolling(window=period, min_periods=period).mean() + 1e-10))
        minus_di = 100 * (minus_dm.rolling(window=period, min_periods=period).mean() / 
                         (tr.rolling(window=period, min_periods=period).mean() + 1e-10))
        
        di_diff = abs(plus_di - minus_di)
        di_sum = plus_di + minus_di
        
        dx = 100 * (di_diff / (di_sum + 1e-10))
        adx = dx.rolling(window=period, min_periods=period).mean()
        
        return adx
    
    @staticmethod
    def cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
        """Commodity Channel Index"""
        typical_price = (high + low + close) / 3
        sma_tp = typical_price.rolling(window=period, min_periods=period).mean()
        mad = typical_price.rolling(window=period, min_periods=period).apply(
            lambda x: np.mean(np.abs(x - x.mean())), raw=False
        )
        
        cci = (typical_price - sma_tp) / (0.015 * mad + 1e-10)
        return cci


# ============================================================================
# PRICE ACTION FEATURES
# ============================================================================

class PriceActionFeatures:
    """Price action and candlestick pattern features"""
    
    @staticmethod
    def candle_body_pct(df: pd.DataFrame) -> pd.Series:
        """Body size as % of total range"""
        body = abs(df['close'] - df['open'])
        range_size = df['high'] - df['low']
        return body / (range_size + 1e-10)
    
    @staticmethod
    def upper_wick_pct(df: pd.DataFrame) -> pd.Series:
        """Upper wick as % of candle"""
        upper_wick = df['high'] - df[['open', 'close']].max(axis=1)
        candle_range = df['high'] - df['low']
        return upper_wick / (candle_range + 1e-10)
    
    @staticmethod
    def lower_wick_pct(df: pd.DataFrame) -> pd.Series:
        """Lower wick as % of candle"""
        lower_wick = df[['open', 'close']].min(axis=1) - df['low']
        candle_range = df['high'] - df['low']
        return lower_wick / (candle_range + 1e-10)
    
    @staticmethod
    def close_position_pct(df: pd.DataFrame) -> pd.Series:
        """Close position in candle (0=bottom, 1=top)"""
        return (df['close'] - df['low']) / (df['high'] - df['low'] + 1e-10)
    
    @staticmethod
    def engulfing_pattern(df: pd.DataFrame) -> pd.Series:
        """Bullish (1) / Bearish (-1) engulfing, None (0)"""
        body_curr = abs(df['close'] - df['open'])
        body_prev = abs(df['close'].shift(1) - df['open'].shift(1))
        
        # Bullish engulfing
        bullish = ((df['open'].shift(1) > df['close'].shift(1)) &  # Prev bearish
                  (df['close'] > df['open']) &  # Curr bullish
                  (df['open'] <= df['close'].shift(1)) &
                  (df['close'] >= df['open'].shift(1)))
        
        # Bearish engulfing
        bearish = ((df['open'].shift(1) < df['close'].shift(1)) &  # Prev bullish
                  (df['close'] < df['open']) &  # Curr bearish
                  (df['open'] >= df['close'].shift(1)) &
                  (df['close'] <= df['open'].shift(1)))
        
        pattern = pd.Series(0, index=df.index)
        pattern[bullish] = 1
        pattern[bearish] = -1
        
        return pattern
    
    @staticmethod
    def pin_bar_pattern(df: pd.DataFrame, threshold: float = 0.33) -> pd.Series:
        """Pin bar detection (small body, long wick)"""
        body = abs(df['close'] - df['open'])
        range_size = df['high'] - df['low']
        
        upper_wick = df['high'] - df[['open', 'close']].max(axis=1)
        lower_wick = df[['open', 'close']].min(axis=1) - df['low']
        
        # Small body
        small_body = body < (range_size * threshold)
        
        # Long wick (2x+ body size)
        long_wick = (upper_wick > body * 2) | (lower_wick > body * 2)
        
        pin_bar = (small_body & long_wick).astype(int)
        return pin_bar
    
    @staticmethod
    def inside_bar_pattern(df: pd.DataFrame) -> pd.Series:
        """Inside bar (IB) - lower high and higher low than previous"""
        inside_bar = ((df['high'] < df['high'].shift(1)) & 
                     (df['low'] > df['low'].shift(1))).astype(int)
        return inside_bar


# ============================================================================
# MARKET MICROSTRUCTURE FEATURES
# ============================================================================

class MarketMicrostructure:
    """Volume and spread dynamics"""
    
    @staticmethod
    def volume_indicators(df: pd.DataFrame, sma_period: int = 20) -> Dict[str, pd.Series]:
        """Volume-based indicators"""
        volume_sma = df['volume'].rolling(window=sma_period, min_periods=sma_period).mean()
        volume_ratio = df['volume'] / (volume_sma + 1e-10)
        
        # Volume trend (increasing/decreasing)
        volume_trend = df['volume'].diff() > 0
        
        return {
            'volume_sma': volume_sma,
            'volume_ratio': volume_ratio,
            'volume_trend': volume_trend.astype(int)
        }
    
    @staticmethod
    def volatility_indicators(df: pd.DataFrame, 
                             period: int = 20, 
                             lookback: int = 100) -> Dict[str, pd.Series]:
        """Volatility clustering and regime"""
        returns = df['close'].pct_change()
        
        # Historical volatility (std of returns)
        volatility = returns.rolling(window=period, min_periods=period).std()
        
        # Volatility ratio (relative to longer period)
        long_vol = returns.rolling(window=lookback, min_periods=lookback).std()
        vol_ratio = volatility / (long_vol + 1e-10)
        
        # Volatility change (increasing/decreasing)
        vol_trend = volatility.diff() > 0
        
        return {
            'volatility': volatility,
            'volatility_ratio': vol_ratio,
            'volatility_trend': vol_trend.astype(int)
        }
    
    @staticmethod
    def high_low_spread(df: pd.DataFrame) -> pd.Series:
        """High-low spread as % of close"""
        spread = (df['high'] - df['low']) / (df['close'] + 1e-10)
        return spread


# ============================================================================
# MAIN FEATURE ENGINE CLASS
# ============================================================================

class FeatureEngine:
    """
    Unified feature engineering pipeline
    
    Combines technical indicators, price action, and market microstructure features
    with optional normalization for ML models.
    """
    
    def __init__(self, df: pd.DataFrame, config: Dict = FEATURES):
        """
        Initialize feature engine
        
        Args:
            df: DataFrame with OHLCV data (timestamp, open, high, low, close, volume)
            config: Feature configuration dict
        """
        
        if df is None or df.empty:
            raise ValueError("DataFrame cannot be empty")
        
        self.df = df.copy()
        self.config = config
        self.features = pd.DataFrame(index=df.index)
        self.features_normalized = None
        self.scaler = None
        
        logger.info(f"FeatureEngine initialized with {len(df)} candles")
    
    def add_technical_indicators(self) -> 'FeatureEngine':
        """Add technical indicators to feature set"""
        try:
            close = self.df['close']
            high = self.df['high']
            low = self.df['low']
            
            config = self.config['technical_indicators']
            
            # RSI
            self.features['rsi_14'] = TechnicalIndicators.rsi(close, config['rsi_period'])
            
            # MACD
            macd, signal, hist = TechnicalIndicators.macd(
                close,
                fast=config['macd_fast'],
                slow=config['macd_slow'],
                signal=config['macd_signal']
            )
            self.features['macd'] = macd
            self.features['macd_signal'] = signal
            self.features['macd_hist'] = hist
            
            # ATR
            self.features['atr_14'] = TechnicalIndicators.atr(high, low, close, config['atr_period'])
            
            # Bollinger Bands
            bb_upper, bb_middle, bb_lower = TechnicalIndicators.bollinger_bands(
                close, config['bb_period']
            )
            self.features['bb_upper'] = bb_upper
            self.features['bb_middle'] = bb_middle
            self.features['bb_lower'] = bb_lower
            self.features['bb_width'] = (bb_upper - bb_lower) / (bb_middle + 1e-10)
            
            # SMAs
            for period in config['sma_periods']:
                self.features[f'sma_{period}'] = TechnicalIndicators.sma(close, period)
            
            # Stochastic
            k_percent, d_percent = TechnicalIndicators.stochastic(high, low, close)
            self.features['stoch_k'] = k_percent
            self.features['stoch_d'] = d_percent
            
            # ADX
            self.features['adx'] = TechnicalIndicators.adx(high, low, close)
            
            # CCI
            self.features['cci'] = TechnicalIndicators.cci(high, low, close)
            
            logger.info("Added technical indicators")
            return self
        
        except Exception as e:
            logger.error(f"Error adding technical indicators: {e}\n{traceback.format_exc()}")
            raise
    
    def add_price_action_features(self) -> 'FeatureEngine':
        """Add price action features"""
        try:
            # Candle structure
            self.features['body_pct'] = PriceActionFeatures.candle_body_pct(self.df)
            self.features['upper_wick_pct'] = PriceActionFeatures.upper_wick_pct(self.df)
            self.features['lower_wick_pct'] = PriceActionFeatures.lower_wick_pct(self.df)
            self.features['close_position_pct'] = PriceActionFeatures.close_position_pct(self.df)
            
            # Patterns
            self.features['engulfing'] = PriceActionFeatures.engulfing_pattern(self.df)
            self.features['pin_bar'] = PriceActionFeatures.pin_bar_pattern(self.df)
            self.features['inside_bar'] = PriceActionFeatures.inside_bar_pattern(self.df)
            
            logger.info("Added price action features")
            return self
        
        except Exception as e:
            logger.error(f"Error adding price action features: {e}")
            raise
    
    def add_market_microstructure(self) -> 'FeatureEngine':
        """Add volume and volatility features"""
        try:
            # Volume indicators
            vol_config = self.config.get('volume_indicators', {})
            vol_features = MarketMicrostructure.volume_indicators(
                self.df,
                sma_period=vol_config.get('volume_sma_period', 20)
            )
            for key, values in vol_features.items():
                self.features[key] = values
            
            # Volatility indicators
            vol_cfg = self.config.get('volatility', {})
            vol_feat = MarketMicrostructure.volatility_indicators(
                self.df,
                period=vol_cfg.get('vol_period', 20),
                lookback=vol_cfg.get('vol_lookback', 100)
            )
            for key, values in vol_feat.items():
                self.features[key] = values
            
            # Spread
            self.features['high_low_spread'] = MarketMicrostructure.high_low_spread(self.df)
            
            logger.info("Added market microstructure features")
            return self
        
        except Exception as e:
            logger.error(f"Error adding microstructure features: {e}")
            raise
    
    def normalize(self, method: str = 'standard', fit: bool = True) -> 'FeatureEngine':
        """
        Normalize features for ML models.

        Args:
            method: 'standard' (StandardScaler) or 'minmax'
            fit: If True, fit the scaler on this data (training). If False, only
                 transform using an already-fitted scaler (walk-forward test folds).
        """
        try:
            features_clean = self.features.dropna(axis=1, how='all')
            features_clean = features_clean.ffill().bfill()

            if method == 'standard':
                if self.scaler is None:
                    self.scaler = StandardScaler()
                if fit:
                    scaled = self.scaler.fit_transform(features_clean)
                else:
                    scaled = self.scaler.transform(features_clean)
                self.features_normalized = pd.DataFrame(
                    scaled,
                    columns=features_clean.columns,
                    index=features_clean.index
                )

            logger.info(f"{'Fit+' if fit else ''}Transformed {len(features_clean.columns)} features (method: {method})")
            return self

        except Exception as e:
            logger.error(f"Normalization error: {e}")
            raise
    
    def save_scaler(self, filepath: str) -> None:
        """Persist this engine's fitted scaler so a later inference-time
        FeatureEngine can transform with the exact statistics training used,
        instead of re-fitting on whatever small window happens to be live."""
        if self.scaler is None:
            raise ValueError("Cannot save an unfit scaler — call normalize(fit=True) first")
        save_scaler(self.scaler, filepath)

    def load_scaler(self, filepath: str) -> 'FeatureEngine':
        """Load a previously-fitted scaler so normalize(fit=False) transforms
        against training-time statistics rather than fitting fresh ones."""
        self.scaler = load_scaler(filepath)
        return self

    def get_features(self, normalized: bool = False) -> pd.DataFrame:
        """
        Get feature matrix
        
        Args:
            normalized: Return normalized features if available
        
        Returns:
            Feature DataFrame
        """
        if normalized and self.features_normalized is not None:
            return self.features_normalized
        return self.features
    
    def summary(self) -> Dict:
        """Print feature engineering summary"""
        summary = {
            'total_features': len(self.features.columns),
            'feature_names': list(self.features.columns),
            'nan_count': self.features.isna().sum().to_dict(),
            'normalized': self.features_normalized is not None
        }
        return summary


# ============================================================================
# SCALER PERSISTENCE
# ============================================================================

def save_scaler(scaler: StandardScaler, filepath: str) -> None:
    """Persist a fitted feature scaler to disk. Creates parent dirs."""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "wb") as f:
        pickle.dump(scaler, f)


def load_scaler(filepath: str) -> StandardScaler:
    """Load a previously-fitted feature scaler from disk."""
    with open(filepath, "rb") as f:
        return pickle.load(f)


# ============================================================================
# HELPER FUNCTION
# ============================================================================

def engineer_features(df: pd.DataFrame,
                     normalize: bool = True,
                     config: Dict = FEATURES) -> Tuple[pd.DataFrame, FeatureEngine]:
    """
    One-shot feature engineering
    
    Args:
        df: OHLCV DataFrame
        normalize: Apply normalization
        config: Feature config
    
    Returns:
        (features_df, engine_object)
    """
    
    try:
        engine = FeatureEngine(df, config)
        engine.add_technical_indicators() \
              .add_price_action_features() \
              .add_market_microstructure()
        
        if normalize:
            engine.normalize()
            return engine.get_features(normalized=True), engine
        
        return engine.get_features(), engine
    
    except Exception as e:
        logger.error(f"Feature engineering failed: {e}")
        raise


if __name__ == '__main__':
    print("Feature Engineering Module - Testing")
    print("=" * 60)
    
    # Create dummy OHLCV data
    dates = pd.date_range(start='2023-01-01', periods=500, freq='4H')
    np.random.seed(42)
    close = 1.0500 + np.cumsum(np.random.randn(500) * 0.0005)
    
    df = pd.DataFrame({
        'timestamp': dates,
        'open': close + np.random.randn(500) * 0.0002,
        'high': close + abs(np.random.randn(500) * 0.0003),
        'low': close - abs(np.random.randn(500) * 0.0003),
        'close': close,
        'volume': np.random.randint(1000, 10000, 500)
    })
    
    df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
    
    # Test feature engineering
    engine = FeatureEngine(df)
    engine.add_technical_indicators() \
          .add_price_action_features() \
          .add_market_microstructure() \
          .normalize()
    
    print(f"✅ Generated {len(engine.features.columns)} features")
    print(f"✅ Normalized: {engine.features_normalized is not None}")
    print("\nFeature names:")
    for i, col in enumerate(engine.features.columns, 1):
        print(f"  {i:2d}. {col}")
