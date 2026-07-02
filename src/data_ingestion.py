"""
Data Ingestion Module - Phase 1
Fetches historical and real-time OHLCV data from MT5, stores in PostgreSQL, caches in Redis.
"""

import pandas as pd
import numpy as np
import psycopg2
from psycopg2.extras import execute_batch
import redis
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict
import json
import traceback

# Try importing MT5, but allow graceful fallback for development
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    logging.warning("MetaTrader5 not available - using CSV fallback")

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.config import (
    POSTGRESQL, REDIS, MT5, MT5_CREDENTIALS, SYMBOLS,
    DATA_CONFIG, LOGS_DIR, DATA_DIR
)

# ============================================================================
# LOGGER SETUP
# ============================================================================

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
try:
    fh = logging.FileHandler(LOGS_DIR / 'data_ingestion.log')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)
except OSError:
    pass
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(formatter)
logger.addHandler(ch)

# ============================================================================
# DATA INGESTION CLASS
# ============================================================================

class ForexDataPipeline:
    """
    Unified data pipeline for forex OHLCV data.
    
    Features:
    - MT5 historical data fetching with retry logic
    - PostgreSQL persistence with efficient batch inserts
    - Redis caching for real-time market data
    - Error handling and recovery
    """
    
    def __init__(self, 
                 db_config: Dict = POSTGRESQL,
                 redis_config: Dict = REDIS,
                 mt5_config: Dict = MT5):
        """Initialize data pipeline"""
        
        self.db_config = db_config
        self.redis_config = redis_config
        self.mt5_config = mt5_config
        
        self.db_conn = None
        self.redis_conn = None
        self.mt5_initialized = False
        
        self._connect_postgres()
        self._connect_redis()
        if MT5_AVAILABLE:
            self._initialize_mt5()
        
        logger.info("ForexDataPipeline initialized")
    
    def _connect_postgres(self):
        """Establish PostgreSQL connection"""
        try:
            self.db_conn = psycopg2.connect(
                dbname=self.db_config['dbname'],
                user=self.db_config['user'],
                password=self.db_config['password'],
                host=self.db_config['host'],
                port=self.db_config['port']
            )
            logger.info(f"PostgreSQL connected: {self.db_config['host']}")
        except psycopg2.Error as e:
            logger.error(f"PostgreSQL connection failed: {e}")
            raise
    
    def _connect_redis(self):
        """Establish Redis connection"""
        try:
            self.redis_conn = redis.Redis(
                host=self.redis_config['host'],
                port=self.redis_config['port'],
                db=self.redis_config['db'],
                decode_responses=self.redis_config['decode_responses'],
                socket_connect_timeout=self.redis_config['socket_connect_timeout']
            )
            self.redis_conn.ping()
            logger.info(f"Redis connected: {self.redis_config['host']}:{self.redis_config['port']}")
        except redis.ConnectionError as e:
            logger.error(f"Redis connection failed: {e}")
            raise
    
    def _initialize_mt5(self) -> bool:
        """Initialize MetaTrader 5 connection"""
        try:
            if not MT5_AVAILABLE:
                logger.warning("MT5 not available")
                return False
            
            if not mt5.initialize(
                path=self.mt5_config['path'],
                login=MT5_CREDENTIALS['account'],
                password=MT5_CREDENTIALS['password'],
                server=MT5_CREDENTIALS['server']
            ):
                logger.error(f"MT5 init failed: {mt5.last_error()}")
                return False

            # Wait for terminal to finish syncing account data (Netting VPS quirk)
            acc = None
            for attempt in range(10):
                acc = mt5.account_info()
                if acc is not None:
                    break
                logger.info(f"Waiting for account sync... attempt {attempt+1}/10")
                time.sleep(2)
            if acc is None:
                logger.error("MT5 initialized but account_info() never returned data — disconnecting")
                mt5.shutdown()
                return False

            self.mt5_initialized = True
            logger.info(f"MT5 initialized — Balance: {acc.balance:.2f} {acc.currency}")
            return True
        
        except Exception as e:
            logger.error(f"MT5 initialization exception: {e}")
            return False
    
    def create_tables(self):
        """Create PostgreSQL tables for OHLCV data"""
        cursor = self.db_conn.cursor()
        
        for symbol in SYMBOLS.keys():
            table_name = f"ohlcv_{symbol.lower()}"
            
            create_sql = f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id BIGSERIAL PRIMARY KEY,
                symbol VARCHAR(10) NOT NULL,
                timeframe INT NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                open DOUBLE PRECISION NOT NULL,
                high DOUBLE PRECISION NOT NULL,
                low DOUBLE PRECISION NOT NULL,
                close DOUBLE PRECISION NOT NULL,
                volume BIGINT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, timeframe, timestamp)
            );
            
            CREATE INDEX IF NOT EXISTS idx_{symbol.lower()}_ts 
                ON {table_name}(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_{symbol.lower()}_tf 
                ON {table_name}(timeframe, timestamp DESC);
            """
            
            try:
                cursor.execute(create_sql)
                logger.info(f"Table created/verified: {table_name}")
            except psycopg2.Error as e:
                logger.error(f"Table creation error ({table_name}): {e}")
        
        self.db_conn.commit()
        cursor.close()
    
    def fetch_historical_data(self, 
                             symbol: str, 
                             timeframe: int, 
                             days: int = DATA_CONFIG['historical_days']) -> Optional[pd.DataFrame]:
        """
        Fetch historical OHLCV data from MT5
        
        Args:
            symbol: Trading pair (e.g., 'EURUSD')
            timeframe: Candle timeframe in minutes (240=4H, 1440=Daily)
            days: Number of days of historical data
        
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        
        if not self.mt5_initialized:
            logger.warning(f"MT5 not initialized, cannot fetch {symbol}")
            return None
        
        try:
            # Calculate number of bars needed
            bars_needed = (days * 1440) // timeframe
            
            logger.info(f"Fetching {symbol} {timeframe}m ({days}d = {bars_needed} bars)")
            
            # Map minutes to MT5 timeframe constants
            mt5_tf = mt5.TIMEFRAME_H4 if timeframe == 240 else mt5.TIMEFRAME_D1
            
            # Fetch from MT5
            rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, bars_needed)
            
            if rates is None or len(rates) == 0:
                logger.error(f"No data returned for {symbol}")
                return None
            
            # Convert to DataFrame
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            
            # Select and rename columns
            df = df[['time', 'open', 'high', 'low', 'close', 'tick_volume']].copy()
            df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            
            # Data validation
            df = df[(df['high'] >= df['low']) & 
                   (df['open'] >= df['low']) & 
                   (df['close'] >= df['low']) &
                   (df['volume'] > 0)]
            
            logger.info(f"Fetched {len(df)} bars for {symbol} {timeframe}m")
            return df
        
        except Exception as e:
            logger.error(f"MT5 fetch error ({symbol}): {e}\n{traceback.format_exc()}")
            return None
    
    def fetch_historical_data_csv(self, symbol: str, timeframe: int) -> Optional[pd.DataFrame]:
        """
        Fallback: Load historical data from CSV (for development/testing)
        Expected file format: data/{SYMBOL}_{TIMEFRAME}.csv
        with columns: timestamp, open, high, low, close, volume
        """
        
        file_path = DATA_DIR / f"{symbol}_{timeframe}.csv"
        
        if not file_path.exists():
            logger.warning(f"CSV file not found: {file_path}")
            return None
        
        try:
            df = pd.read_csv(file_path)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            logger.info(f"Loaded {len(df)} bars from {file_path}")
            return df
        
        except Exception as e:
            logger.error(f"CSV load error: {e}")
            return None
    
    def store_ohlcv(self, 
                   symbol: str, 
                   timeframe: int, 
                   df: pd.DataFrame) -> int:
        """
        Store OHLCV data in PostgreSQL with batch inserts
        
        Args:
            symbol: Trading pair
            timeframe: Candle timeframe in minutes
            df: DataFrame with OHLCV data
        
        Returns:
            Number of records inserted
        """
        
        if df is None or df.empty:
            logger.warning(f"Empty DataFrame for {symbol} {timeframe}")
            return 0
        
        table_name = f"ohlcv_{symbol.lower()}"
        cursor = self.db_conn.cursor()
        
        insert_sql = f"""
        INSERT INTO {table_name} 
        (symbol, timeframe, timestamp, open, high, low, close, volume)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (symbol, timeframe, timestamp) DO NOTHING;
        """
        
        try:
            records_to_insert = []
            for _, row in df.iterrows():
                records_to_insert.append((
                    symbol,
                    timeframe,
                    row['timestamp'],
                    float(row['open']),
                    float(row['high']),
                    float(row['low']),
                    float(row['close']),
                    int(row['volume'])
                ))
            
            # Batch insert
            execute_batch(cursor, insert_sql, records_to_insert, 
                         page_size=DATA_CONFIG['batch_size'])
            self.db_conn.commit()
            
            logger.info(f"Inserted {len(records_to_insert)} records: {symbol} {timeframe}m")
            return len(records_to_insert)
        
        except psycopg2.Error as e:
            self.db_conn.rollback()
            logger.error(f"Insert error ({symbol}): {e}")
            return 0
        finally:
            cursor.close()
    
    def get_ohlcv(self, 
                 symbol: str, 
                 timeframe: int, 
                 limit: int = 500,
                 start_date: Optional[datetime] = None) -> Optional[pd.DataFrame]:
        """
        Retrieve OHLCV data from PostgreSQL
        
        Args:
            symbol: Trading pair
            timeframe: Candle timeframe in minutes
            limit: Max records to retrieve
            start_date: Optional start date filter
        
        Returns:
            DataFrame with OHLCV data, sorted by timestamp ascending
        """
        
        table_name = f"ohlcv_{symbol.lower()}"
        
        where_clause = f"WHERE timeframe = {timeframe}"
        if start_date:
            where_clause += f" AND timestamp >= '{start_date}'"
        
        query = f"""
        SELECT timestamp, open, high, low, close, volume
        FROM {table_name}
        {where_clause}
        ORDER BY timestamp ASC
        """
        if limit is not None:
            query += f" LIMIT {limit}"
        
        try:
            df = pd.read_sql_query(query, self.db_conn)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            logger.debug(f"Retrieved {len(df)} records: {symbol} {timeframe}m")
            return df
        
        except psycopg2.Error as e:
            logger.error(f"Query error: {e}")
            return None
    
    def cache_latest_price(self, symbol: str, timeframe: int, price: float, ttl: int = None):
        """Cache latest market price in Redis"""
        ttl = ttl or DATA_CONFIG['cache_ttl']
        key = f"{symbol}:{timeframe}:latest_price"
        
        try:
            self.redis_conn.setex(key, ttl, str(price))
        except redis.RedisError as e:
            logger.error(f"Redis cache error: {e}")
    
    def cache_latest_candles(self, symbol: str, timeframe: int, df: pd.DataFrame, ttl: int = None):
        """Cache latest 100 candles in Redis for quick access"""
        ttl = ttl or DATA_CONFIG['cache_ttl']
        key = f"{symbol}:{timeframe}:latest_100"
        
        try:
            candles_json = df.tail(100).to_json(orient='records')
            self.redis_conn.setex(key, ttl, candles_json)
            logger.debug(f"Cached 100 latest candles: {symbol} {timeframe}m")
        except Exception as e:
            logger.error(f"Cache error: {e}")
    
    def get_cached_candles(self, symbol: str, timeframe: int) -> Optional[pd.DataFrame]:
        """Retrieve cached candles from Redis"""
        key = f"{symbol}:{timeframe}:latest_100"
        
        try:
            cached_data = self.redis_conn.get(key)
            if cached_data:
                df = pd.read_json(cached_data)
                df['timestamp'] = pd.to_datetime(df['timestamp'])
                return df
            return None
        except Exception as e:
            logger.error(f"Cache retrieval error: {e}")
            return None
    
    def close(self):
        """Cleanup connections"""
        if self.db_conn:
            self.db_conn.close()
            logger.info("PostgreSQL connection closed")
        
        if self.mt5_initialized and MT5_AVAILABLE:
            mt5.shutdown()
            logger.info("MT5 connection closed")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# ============================================================================
# HELPER FUNCTION FOR BOOTSTRAP
# ============================================================================

def bootstrap_historical_data(symbols: List[str] = None) -> bool:
    """
    Bootstrap system with historical data for all symbols and timeframes
    Useful for initial setup
    """
    
    symbols = symbols or list(SYMBOLS.keys())
    
    try:
        with ForexDataPipeline() as pipeline:
            # Create tables
            pipeline.create_tables()
            
            # Fetch and store data for each symbol/timeframe
            for symbol in symbols:
                for timeframe in SYMBOLS[symbol]['timeframes']:
                    logger.info(f"\n{'='*60}")
                    logger.info(f"Processing {symbol} {timeframe}m")
                    logger.info(f"{'='*60}")
                    
                    # Fetch data
                    df = pipeline.fetch_historical_data(symbol, timeframe)
                    
                    if df is None:
                        logger.warning(f"Falling back to CSV for {symbol} {timeframe}m")
                        df = pipeline.fetch_historical_data_csv(symbol, timeframe)
                    
                    if df is not None:
                        # Store in PostgreSQL
                        inserted = pipeline.store_ohlcv(symbol, timeframe, df)
                        
                        # Cache latest candles
                        pipeline.cache_latest_candles(symbol, timeframe, df)
                        
                        logger.info(f"{symbol} {timeframe}m: {inserted} records stored")
                    else:
                        logger.error(f"Failed to get data for {symbol} {timeframe}m")
        
        return True
    
    except Exception as e:
        logger.error(f"Bootstrap failed: {e}")
        return False


if __name__ == '__main__':
    print("Data Ingestion Module - Testing")
    print("=" * 60)
    
    # Test connections
    pipeline = ForexDataPipeline()
    print("Connections established")
    
    # Create tables
    pipeline.create_tables()
    print("Tables created")
    
    # For development/testing without MT5:
    bootstrap_historical_data()
    
    pipeline.close()
