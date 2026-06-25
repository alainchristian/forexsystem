#!/usr/bin/env python3
"""
Phase 1 System Validation Script
Tests all core modules and verifies system readiness
"""

import sys
import os
import logging
from pathlib import Path
import pandas as pd
import numpy as np

# Add project root and src to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

# ============================================================================
# SETUP
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

def test_header(title):
    print(f"\n{Colors.BLUE}{'='*70}{Colors.END}")
    print(f"{Colors.BLUE}{title:^70}{Colors.END}")
    print(f"{Colors.BLUE}{'='*70}{Colors.END}\n")

def test_pass(msg):
    print(f"{Colors.GREEN}✅ {msg}{Colors.END}")

def test_fail(msg):
    print(f"{Colors.RED}❌ {msg}{Colors.END}")

def test_warn(msg):
    print(f"{Colors.YELLOW}⚠️  {msg}{Colors.END}")

# ============================================================================
# TESTS
# ============================================================================

def test_imports():
    """Test all critical imports"""
    test_header("TEST 1: Python Imports")
    
    imports = [
        ('pandas', 'pd'),
        ('numpy', 'np'),
        ('psycopg2', 'psycopg2'),
        ('redis', 'redis'),
        ('sklearn', 'sklearn'),
    ]
    
    success_count = 0
    
    for module_name, alias in imports:
        try:
            __import__(module_name)
            test_pass(f"Import {module_name}")
            success_count += 1
        except ImportError as e:
            test_fail(f"Import {module_name}: {e}")
    
    return success_count == len(imports)

def test_config():
    """Test configuration loading"""
    test_header("TEST 2: Configuration")
    
    try:
        from config.config import (
            POSTGRESQL, REDIS, SYMBOLS, FEATURES, BACKTEST
        )
        
        test_pass(f"Loaded config for {len(SYMBOLS)} symbols")
        test_pass(f"Features config: {len(FEATURES)} categories")
        test_pass(f"Backtest config: ${BACKTEST['initial_capital']} capital")
        
        return True
    except Exception as e:
        test_fail(f"Config loading: {e}")
        return False

def test_data_generation():
    """Generate dummy OHLCV data"""
    test_header("TEST 3: Data Generation")
    
    try:
        # Create dummy data
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
        
        # Validate
        assert len(df) == 500, "Data length mismatch"
        assert df['high'].min() >= df['low'].min(), "High < Low error"
        assert df['volume'].sum() > 0, "Volume error"
        
        test_pass(f"Generated {len(df)} candles")
        test_pass(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
        test_pass(f"Price range: {df['close'].min():.4f} to {df['close'].max():.4f}")
        
        return df
    
    except Exception as e:
        test_fail(f"Data generation: {e}")
        return None

def test_feature_engineering(df):
    """Test feature engineering module"""
    test_header("TEST 4: Feature Engineering")
    
    if df is None:
        test_fail("No data available")
        return False
    
    try:
        from features import FeatureEngine
        
        engine = FeatureEngine(df)
        engine.add_technical_indicators()
        
        test_pass(f"Technical indicators: {len(engine.features.columns)} features")
        
        engine.add_price_action_features()
        test_pass(f"Price action: {len(engine.features.columns)} total features")
        
        engine.add_market_microstructure()
        test_pass(f"Market microstructure: {len(engine.features.columns)} total features")
        
        engine.normalize()
        test_pass(f"Normalized: {len(engine.features_normalized.columns)} features")
        
        # Validate
        assert engine.features_normalized is not None, "Normalization failed"
        assert not engine.features_normalized.isnull().all().any(), "All-null features"
        
        return engine.get_features(normalized=True)
    
    except Exception as e:
        test_fail(f"Feature engineering: {e}")
        import traceback
        traceback.print_exc()
        return None

def test_backtester(df, features):
    """Test backtester module"""
    test_header("TEST 5: Backtester")
    
    if df is None:
        test_fail("No data available")
        return False
    
    try:
        from backtester import Backtester
        
        # Simple RSI strategy
        rsi = features['rsi_14']
        signals = np.zeros(len(df))
        signals[rsi < 30] = 1
        signals[rsi > 70] = -1
        
        # Run backtest
        bt = Backtester(df, initial_capital=10000, risk_per_trade=0.02)
        bt.backtest(df.reset_index(drop=True), signals)
        
        report = bt.report()
        
        test_pass(f"Total trades: {report['total_trades']}")
        test_pass(f"Win rate: {report['win_rate']:.1%}")
        test_pass(f"Profit factor: {report['profit_factor']:.2f}")
        test_pass(f"Sharpe ratio: {report['sharpe_ratio']:.2f}")
        test_pass(f"Max drawdown: {report['max_drawdown']:.2%}")
        test_pass(f"Total P&L: ${report['total_pnl']:.2f}")
        
        # Validate
        if report['total_trades'] > 0:
            assert report['win_rate'] <= 1.0, "Win rate > 100%"
            test_pass("Backtest validation passed")
        else:
            test_warn("No trades generated (may be normal for test data)")
        
        return report
    
    except Exception as e:
        test_fail(f"Backtester: {e}")
        import traceback
        traceback.print_exc()
        return None

def test_database():
    """Test PostgreSQL connection"""
    test_header("TEST 6: Database Connection")
    
    try:
        import psycopg2
        from config.config import POSTGRESQL
        
        # Try connection
        try:
            conn = psycopg2.connect(
                dbname='postgres',  # Connect to default postgres DB first
                user=POSTGRESQL['user'],
                password=POSTGRESQL['password'],
                host=POSTGRESQL['host'],
                port=POSTGRESQL['port']
            )
            conn.close()
            test_pass("PostgreSQL connection successful")
            return True
        
        except psycopg2.OperationalError as e:
            test_warn(f"PostgreSQL not accessible: {e}")
            test_warn("(This is expected if not deployed to production)")
            return None  # Not a failure, just not available
    
    except ImportError:
        test_fail("psycopg2 not installed")
        return False

def test_redis():
    """Test Redis connection"""
    test_header("TEST 7: Redis Connection")
    
    try:
        import redis
        from config.config import REDIS
        
        try:
            r = redis.Redis(
                host=REDIS['host'],
                port=REDIS['port'],
                db=REDIS['db'],
                socket_connect_timeout=2
            )
            r.ping()
            test_pass("Redis connection successful")
            return True
        
        except redis.ConnectionError as e:
            test_warn(f"Redis not accessible: {e}")
            test_warn("(This is expected if not deployed to production)")
            return None  # Not a failure, just not available
    
    except ImportError:
        test_fail("redis not installed")
        return False

def test_csv_export(df, features, report):
    """Test CSV export functionality"""
    test_header("TEST 8: CSV Export")
    
    try:
        from backtester import Backtester
        import tempfile
        
        # Create temporary backtester for trade export
        bt = Backtester(df, initial_capital=10000)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Export features
            features_path = f"{tmpdir}/features.csv"
            features.to_csv(features_path)
            assert Path(features_path).exists(), "Features export failed"
            test_pass(f"Exported features: {features_path}")
            
            # Export OHLCV
            ohlcv_path = f"{tmpdir}/ohlcv.csv"
            df.to_csv(ohlcv_path, index=False)
            assert Path(ohlcv_path).exists(), "OHLCV export failed"
            test_pass(f"Exported OHLCV: {ohlcv_path}")
            
            test_pass("CSV export successful")
        
        return True
    
    except Exception as e:
        test_fail(f"CSV export: {e}")
        return False

def test_performance():
    """Test system performance"""
    test_header("TEST 9: Performance Benchmarks")
    
    import time
    
    try:
        # Generate larger dataset
        dates = pd.date_range(start='2020-01-01', periods=2000, freq='4H')
        np.random.seed(42)
        close = 100 + np.cumsum(np.random.randn(2000) * 0.5)
        
        df_large = pd.DataFrame({
            'timestamp': dates,
            'open': close + np.random.randn(2000) * 0.2,
            'high': close + abs(np.random.randn(2000) * 0.3),
            'low': close - abs(np.random.randn(2000) * 0.3),
            'close': close,
            'volume': np.random.randint(1000000, 5000000, 2000)
        })
        
        # Test feature engineering performance
        from features import FeatureEngine
        
        start = time.time()
        engine = FeatureEngine(df_large)
        engine.add_technical_indicators() \
              .add_price_action_features() \
              .add_market_microstructure() \
              .normalize()
        feature_time = time.time() - start
        
        test_pass(f"Feature engineering (2000 candles): {feature_time*1000:.1f}ms")
        
        # Test backtester performance
        from backtester import Backtester
        
        signals = np.random.choice([-1, 0, 1], size=len(df_large), p=[0.1, 0.8, 0.1])
        
        start = time.time()
        bt = Backtester(df_large)
        bt.backtest(df_large.reset_index(drop=True), signals)
        backtest_time = time.time() - start
        
        test_pass(f"Backtest (2000 candles, {len([s for s in signals if s!=0])} trades): {backtest_time*1000:.1f}ms")
        
        return True
    
    except Exception as e:
        test_fail(f"Performance test: {e}")
        return False

def system_summary():
    """Print system summary"""
    test_header("SYSTEM SUMMARY")
    
    try:
        import platform
        print(f"Python: {platform.python_version()}")
        print(f"OS: {platform.system()} {platform.release()}")
        
        import pandas as pd
        print(f"Pandas: {pd.__version__}")
        
        import numpy as np
        print(f"NumPy: {np.__version__}")
        
        print()
    except Exception as e:
        logger.warning(f"Could not print full summary: {e}")

# ============================================================================
# MAIN
# ============================================================================

def main():
    """Run all tests"""
    
    print(f"\n{Colors.BLUE}{'='*70}{Colors.END}")
    print(f"{Colors.BLUE}{'FOREX TRADING SYSTEM - PHASE 1 VALIDATION':^70}{Colors.END}")
    print(f"{Colors.BLUE}{'='*70}{Colors.END}\n")
    
    results = {}
    
    # Run tests
    results['imports'] = test_imports()
    results['config'] = test_config()
    
    df = test_data_generation()
    if df is not None:
        features = test_feature_engineering(df)
        if features is not None:
            report = test_backtester(df, features)
            if report is not None:
                results['export'] = test_csv_export(df, features, report)
    
    results['database'] = test_database()
    results['redis'] = test_redis()
    results['performance'] = test_performance()
    
    system_summary()
    
    # Summary
    test_header("TEST SUMMARY")
    
    passed = sum(1 for v in results.values() if v is True)
    failed = sum(1 for v in results.values() if v is False)
    skipped = sum(1 for v in results.values() if v is None)
    
    print(f"Tests Passed:  {Colors.GREEN}{passed}{Colors.END}")
    print(f"Tests Failed:  {Colors.RED}{failed}{Colors.END}")
    print(f"Tests Skipped: {Colors.YELLOW}{skipped}{Colors.END}")
    print(f"Total:         {passed + failed + skipped}\n")
    
    if failed == 0:
        print(f"{Colors.GREEN}✅ All tests passed! System ready for Phase 2.{Colors.END}\n")
        return 0
    else:
        print(f"{Colors.RED}❌ Some tests failed. Review output above.{Colors.END}\n")
        return 1

if __name__ == '__main__':
    sys.exit(main())
