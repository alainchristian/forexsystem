# PHASE 1 IMPLEMENTATION PROMPT - COPY & PASTE FOR CLAUDE/CLAUDE CODE

## CONTEXT

You are implementing **Phase 1 of an AI Forex Trading System** for a Rwandan technical educator named Alain Christian Majyambere.

**Phase 1 Goal**: Build a stable data pipeline + feature engineering + backtesting framework

**Project Location**: `/home/claude/forex-system/`

**All core files are already created**. Your job is:
1. Verify installation and environment
2. Test all modules
3. Deploy to Hetzner server (if needed)
4. Document any issues
5. Prepare for Phase 2

---

## YOUR TASK

### STEP 1: Verify Files & Structure

Check that all Phase 1 files exist:

```
forex-system/
├── config/config.py                 ✅ Configuration
├── src/
│   ├── data_ingestion.py           ✅ OHLCV pipeline
│   ├── features.py                 ✅ Feature engineering
│   ├── backtester.py               ✅ Walk-forward backtester
├── scripts/
│   ├── init_database.py            ✅ PostgreSQL setup
│   ├── setup_hetzner.sh            ✅ Server initialization
├── tests/
│   ├── test_phase1.py              ✅ Validation tests
├── data/
│   ├── EURUSD_240.csv              ✅ Sample data
├── requirements.txt                ✅ Dependencies
├── README.md                        ✅ Full documentation
└── .env                            ❓ Create this (see STEP 2)
```

**ACTION**: List all files in `/home/claude/forex-system/` recursively and verify structure matches above.

---

### STEP 2: Environment Setup

**2a. Create .env file** (if on local machine or Hetzner):

```bash
cat > /home/claude/forex-system/.env << 'EOF'
# PostgreSQL
FOREX_DB_NAME=forex_trading_db
FOREX_DB_USER=claude
FOREX_DB_PASSWORD=change_me_in_production
FOREX_DB_HOST=localhost
FOREX_DB_PORT=5432

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# MT5 (update before live trading)
MT5_ACCOUNT=0
MT5_PASSWORD=
MT5_SERVER=Exness-MT5
EOF
```

**2b. Create Python virtual environment** (if not using system Python):

```bash
cd /home/claude/forex-system
python3.11 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

**ACTION**: 
- Create `.env` file
- Activate virtual environment
- Install all dependencies from `requirements.txt`
- Verify with: `pip list | grep pandas`

---

### STEP 3: Test Core Modules (Local Testing)

Run the comprehensive Phase 1 validation script:

```bash
cd /home/claude/forex-system
source venv/bin/activate
python tests/test_phase1.py
```

**Expected Output**:
```
====== TEST 1: Python Imports ======
✅ Import pandas
✅ Import numpy
✅ Import psycopg2
✅ Import redis
✅ Import sklearn

====== TEST 2: Configuration ======
✅ Loaded config for 3 symbols
✅ Features config: 3 categories
✅ Backtest config: $10000 capital

====== TEST 3: Data Generation ======
✅ Generated 500 candles
✅ Date range: 2023-01-01 to 2023-05-13
✅ Price range: 0.9995 to 1.1805

====== TEST 4: Feature Engineering ======
✅ Technical indicators: 10 features
✅ Price action: 17 total features
✅ Market microstructure: 23 total features
✅ Normalized: 23 features

====== TEST 5: Backtester ======
✅ Total trades: 45
✅ Win rate: 55.6%
✅ Profit factor: 1.92
✅ Sharpe ratio: 1.34
✅ Max drawdown: 8.20%
✅ Total P&L: $1,234.56

====== TEST 6: Database Connection ======
⚠️ PostgreSQL not accessible: (expected if not deployed)

====== TEST 7: Redis Connection ======
⚠️ Redis not accessible: (expected if not deployed)

====== TEST 8: CSV Export ======
✅ Exported features: /tmp/.../features.csv
✅ Exported OHLCV: /tmp/.../ohlcv.csv
✅ CSV export successful

====== TEST 9: Performance Benchmarks ======
✅ Feature engineering (2000 candles): 245.3ms
✅ Backtest (2000 candles, 182 trades): 128.5ms

====== TEST SUMMARY ======
Tests Passed:  8
Tests Failed:  0
Tests Skipped: 2

✅ All tests passed! System ready for Phase 2.
```

**If any tests FAIL**:
- Review error messages carefully
- Check log files: `tail -f logs/forex_system.log`
- Verify all imports: `python -c "import pandas, numpy, psycopg2, redis"`

**ACTION**: Run `tests/test_phase1.py` and report results. Fix any failures.

---

### STEP 4: Manual Module Testing

Test each module individually to understand the API:

#### 4a. Test Data Ingestion

```bash
python -c "
from src.data_ingestion import ForexDataPipeline

# Test CSV loading (no MT5 required)
pipeline = ForexDataPipeline()

# Load sample CSV data
df = pipeline.fetch_historical_data_csv('EURUSD', timeframe=240)
print(f'Loaded {len(df)} candles')
print(f'Columns: {list(df.columns)}')
print(df.head())
"
```

**Expected Output**:
```
Loaded 96 candles
Columns: ['timestamp', 'open', 'high', 'low', 'close', 'volume']
  timestamp    open     high      low    close   volume
0 2023-01-02 1.05500 1.05600 1.05450 1.05550 125000
1 2023-01-02 1.05550 1.05700 1.05500 1.05650 132000
...
```

#### 4b. Test Feature Engineering

```bash
python -c "
from src.data_ingestion import ForexDataPipeline
from src.features import engineer_features

# Load data
pipeline = ForexDataPipeline()
df = pipeline.fetch_historical_data_csv('EURUSD', timeframe=240)

# Generate features
features, engine = engineer_features(df, normalize=True)

print(f'Generated {len(features.columns)} features')
print(f'Feature names:')
for i, col in enumerate(features.columns, 1):
    print(f'  {i:2d}. {col}')

# Show sample
print(f'\nSample (last row):')
print(features.iloc[-1].head(10))
"
```

**Expected Output**:
```
Generated 23 features
Feature names:
  1. rsi_14
  2. macd
  3. macd_signal
  ...
  23. high_low_spread

Sample (last row):
rsi_14                 0.856234
macd                  -0.342123
...
```

#### 4c. Test Backtester

```bash
python -c "
from src.data_ingestion import ForexDataPipeline
from src.features import engineer_features
from src.backtester import Backtester
import numpy as np

# Load data
pipeline = ForexDataPipeline()
df = pipeline.fetch_historical_data_csv('EURUSD', timeframe=240)

# Generate features
features, _ = engineer_features(df, normalize=True)

# Create simple RSI strategy
rsi = features['rsi_14']
signals = np.zeros(len(df))
signals[rsi < 30] = 1    # Buy oversold
signals[rsi > 70] = -1   # Sell overbought

# Run backtest
bt = Backtester(df, initial_capital=10000, risk_per_trade=0.02)
bt.backtest(df.reset_index(drop=True), signals)

# Print report
report = bt.report()
print('='*50)
print('BACKTEST RESULTS')
print('='*50)
for key, value in sorted(report.items()):
    if isinstance(value, float):
        if 'rate' in key or 'pct' in key:
            print(f'{key:.<30} {value:.2%}')
        else:
            print(f'{key:.<30} {value:.4f}')
    else:
        print(f'{key:.<30} {value}')
"
```

**Expected Output**:
```
==================================================
BACKTEST RESULTS
==================================================
avg_loss............................. 45.2345
avg_win.............................. 78.5432
consecutive_losses................... 3
consecutive_wins..................... 5
gross_loss........................... -234.5678
gross_profit......................... 567.8901
losing_trades........................ 12
max_drawdown......................... 5.23%
profit_factor........................ 2.42
recovery_factor...................... 15.34
sharpe_ratio......................... 1.45
total_pnl............................ 333.3223
total_pnl_pct........................ 3.33%
total_trades......................... 20
win_rate............................. 55.00%
winning_trades....................... 11
```

**ACTION**: Run all 3 module tests. Verify output matches expected results.

---

### STEP 5: Deploy to Hetzner (Optional - For Production)

If deploying to Hetzner Ubuntu 24.04:

```bash
# SSH into Hetzner
ssh root@your_hetzner_ip

# Download and run setup script
cd /tmp
wget https://raw.githubusercontent.com/your-repo/setup_hetzner.sh
sudo bash setup_hetzner.sh

# Wait ~10 minutes for completion
# Script will:
# ✅ Install Python 3.11
# ✅ Install PostgreSQL 14
# ✅ Install Redis
# ✅ Install all Python dependencies
# ✅ Configure systemd services
# ✅ Setup firewall
# ✅ Create .env file

# After setup completes:
cd /home/claude/forex-system
source venv/bin/activate

# Initialize database
python scripts/init_database.py

# Run tests
python tests/test_phase1.py
```

**ACTION** (if deploying): Run setup script and verify all steps complete.

---

### STEP 6: Document Issues & Solutions

Create `docs/PHASE1_NOTES.md` with:

1. **Environment Details**:
   - OS (Windows/Mac/Linux)
   - Python version
   - Database version (PostgreSQL/MySQL)
   - Any errors during setup

2. **Test Results**:
   - Which tests passed/failed
   - Performance metrics
   - Sample output

3. **Issues Encountered & Solutions**:
   ```
   **Issue**: "ModuleNotFoundError: No module named 'psycopg2'"
   **Solution**: pip install psycopg2-binary
   
   **Issue**: "PostgreSQL not accessible"
   **Solution**: This is expected for local testing. Skip database tests.
   
   **Issue**: Feature engineering takes >1 second
   **Solution**: Expected for large datasets. Optimize later in Phase 3.
   ```

**ACTION**: Document your setup process and any issues.

---

### STEP 7: Prepare for Phase 2

Create `docs/PHASE2_READINESS.md`:

```markdown
# Phase 2 Readiness Checklist

## Phase 1 Completion Status
- ✅ Data pipeline tested and working
- ✅ Feature engineering generating 23+ indicators
- ✅ Backtester producing valid metrics
- ✅ Infrastructure ready (or local dev complete)

## Phase 2 Goals
1. **LSTM Price Predictor** (Week 1-2)
   - TensorFlow/Keras
   - Lookback window: 60 candles
   - Target: Predict next close price

2. **XGBoost Signal Generator** (Week 2-3)
   - Multi-class classification (1=UP, 0=FLAT, -1=DOWN)
   - Cross-validation for robustness
   - Feature importance analysis

3. **Ensemble Strategy** (Week 3)
   - Combine LSTM + XGBoost
   - Confidence thresholding
   - Walk-forward validation on ensemble

## Data Ready for Phase 2
- ✅ 500+ candles for feature engineering
- ✅ Historical data loaded in PostgreSQL (optional)
- ✅ CSV fallback available (data/EURUSD_240.csv)

## Infrastructure Status
- PostgreSQL: {status}
- Redis: {status}
- Hetzner: {status}

## Known Limitations
- MT5 only on Windows (Linux uses CSV)
- Single symbol (EURUSD) for now
- 4H timeframe primary

## Next Actions
1. Implement LSTM model in Phase 2
2. Train on 6+ months historical data
3. Validate with walk-forward backtesting
4. Prepare for live trading engine (Phase 4)
```

**ACTION**: Create readiness checklist documenting current status.

---

## QUICK REFERENCE COMMANDS

```bash
# Activate environment
source /home/claude/forex-system/venv/bin/activate

# Run all tests
python /home/claude/forex-system/tests/test_phase1.py

# Test individual modules
python /home/claude/forex-system/src/data_ingestion.py
python /home/claude/forex-system/src/features.py
python /home/claude/forex-system/src/backtester.py

# View logs
tail -f /home/claude/forex-system/logs/forex_system.log

# Install dependencies
pip install -r /home/claude/forex-system/requirements.txt

# Initialize database (Hetzner only)
python /home/claude/forex-system/scripts/init_database.py

# Check configuration
python -c "from config.config import SYMBOLS, BACKTEST; print(SYMBOLS); print(BACKTEST)"
```

---

## SUCCESS CRITERIA

Phase 1 is **COMPLETE** when:

1. ✅ All 9 tests in `test_phase1.py` pass (except DB/Redis if local)
2. ✅ Feature engineering generates 20+ indicators without errors
3. ✅ Backtester produces realistic metrics (Sharpe > 0.5, Win rate > 40%)
4. ✅ Data loads from CSV without MT5
5. ✅ Documentation complete with issues logged
6. ✅ Hetzner deployment (optional) fully automated

---

## SUPPORT RESOURCES

- **README.md**: Full documentation with examples
- **config/config.py**: All configuration options
- **logs/**: Check logs for detailed error messages
- **src/**: Each module has docstrings and examples

---

## PHASE 1 STATUS

**Start Date**: [TODAY]
**Target Completion**: [+1 week for testing]
**Estimated Time**: 4-8 hours

**What's Next After Phase 1?**
→ Phase 2: ML Models (LSTM + XGBoost)
→ Phase 3: Risk Management & Live Engine
→ Phase 4: Dashboard & Production Deployment

---

## FINAL CHECKLIST

Before moving to Phase 2:

```
☐ All core files created and organized
☐ Virtual environment activated and dependencies installed
☐ test_phase1.py runs with 8/9 tests passing
☐ Data loading works (CSV or MT5)
☐ Features generate without errors
☐ Backtester produces valid metrics
☐ Environment variables configured (.env)
☐ Database initialized (if Hetzner)
☐ Logs reviewed and issues documented
☐ Hetzner deployed and tested (optional)
☐ Phase 1 documentation complete
```

---

**Now run your first test and report back with results!**
