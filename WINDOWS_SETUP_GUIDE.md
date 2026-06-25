# FOREX TRADING SYSTEM - WINDOWS SETUP GUIDE

## Setup for: `C:\Users\Christian\Desktop\projects`

### STEP 1: Create Project Directory

```powershell
# Open PowerShell as Administrator
# Press Win+X, select "Windows PowerShell (Admin)"

# Create directory structure
New-Item -ItemType Directory -Path "C:\Users\Christian\Desktop\projects\forex-system" -Force

# Navigate to project
cd C:\Users\Christian\Desktop\projects\forex-system
```

### STEP 2: Download All Files

**Option A: Download ZIP from Claude outputs** (Easiest)
1. Download the 3 documentation files from Claude
2. Extract to `C:\Users\Christian\Desktop\projects\`
3. Copy all source code files there

**Option B: Copy from /home/claude/forex-system** (If on same network)
```powershell
# Copy all Phase 1 files
Copy-Item -Path "\\path\to\forex-system\*" -Destination "C:\Users\Christian\Desktop\projects\forex-system" -Recurse
```

**Option C: Create files manually** (See file list below)

---

### STEP 3: Verify Directory Structure

After copying, you should have:

```
C:\Users\Christian\Desktop\projects\forex-system\
├── README.md
├── PHASE1_DELIVERY_SUMMARY.md
├── PHASE1_IMPLEMENTATION_PROMPT.md
├── requirements.txt
├── config/
│   └── config.py
├── src/
│   ├── data_ingestion.py
│   ├── features.py
│   └── backtester.py
├── scripts/
│   ├── init_database.py
│   └── setup_hetzner.sh
├── tests/
│   └── test_phase1.py
├── data/
│   └── EURUSD_240.csv
└── logs/
```

**To verify**, run in PowerShell:
```powershell
cd C:\Users\Christian\Desktop\projects\forex-system
Get-ChildItem -Recurse | Select-Object FullName
```

---

### STEP 4: Install Python 3.11+

**Check if Python is installed:**
```powershell
python --version
```

**If not installed:**
1. Download from https://www.python.org/downloads/
2. Install Python 3.11+ (CHECK: "Add Python to PATH")
3. Verify: `python --version`

---

### STEP 5: Create Virtual Environment

```powershell
# Navigate to project
cd C:\Users\Christian\Desktop\projects\forex-system

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On PowerShell:
.\venv\Scripts\Activate.ps1

# If you get execution policy error, run:
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Then try again:
.\venv\Scripts\Activate.ps1

# You should see (venv) at start of prompt:
# (venv) C:\Users\Christian\Desktop\projects\forex-system>
```

**Troubleshooting activation:**
```powershell
# If Activate.ps1 fails, try batch file instead:
.\venv\Scripts\activate.bat

# Or use Python directly:
python -m venv venv
python -m pip install --upgrade pip
```

---

### STEP 6: Install Dependencies

```powershell
# Make sure venv is activated (you should see (venv) in prompt)

# Upgrade pip
python -m pip install --upgrade pip

# Install all requirements
pip install -r requirements.txt

# This will take 2-5 minutes
# You'll see: "Successfully installed pandas, numpy, psycopg2, ..."
```

**Check installation:**
```powershell
pip list | findstr pandas
# Should show: pandas 2.0.3
```

---

### STEP 7: Create .env File

```powershell
# Create .env with your settings
New-Item -Path "C:\Users\Christian\Desktop\projects\forex-system\.env" -ItemType File

# Edit it with Notepad
notepad C:\Users\Christian\Desktop\projects\forex-system\.env
```

**Paste this into the file:**
```
# PostgreSQL (optional for Phase 1)
FOREX_DB_NAME=forex_trading_db
FOREX_DB_USER=claude
FOREX_DB_PASSWORD=change_me_in_production
FOREX_DB_HOST=localhost
FOREX_DB_PORT=5432

# Redis (optional for Phase 1)
REDIS_HOST=localhost
REDIS_PORT=6379

# MT5 (update before live trading - Windows only!)
MT5_ACCOUNT=0
MT5_PASSWORD=
MT5_SERVER=Exness-MT5
```

Save and close Notepad.

---

### STEP 8: Run Tests

```powershell
# Make sure venv is activated
cd C:\Users\Christian\Desktop\projects\forex-system

# Run validation tests
python tests\test_phase1.py

# Wait for results...
```

**Expected output** (takes ~30 seconds):
```
======================================
TEST 1: Python Imports
======================================
✅ Import pandas
✅ Import numpy
✅ Import psycopg2
✅ Import redis
✅ Import sklearn

======================================
TEST 2: Configuration
======================================
✅ Loaded config for 3 symbols
✅ Features config: 3 categories
✅ Backtest config: $10000 capital

[... more tests ...]

✅ All tests passed! System ready for Phase 2.
```

---

### STEP 9: Test Individual Modules

```powershell
# Still in venv, in the project directory

# Test 1: Data Loading
python -c "
from src.data_ingestion import ForexDataPipeline
pipeline = ForexDataPipeline()
df = pipeline.fetch_historical_data_csv('EURUSD', timeframe=240)
print(f'✅ Loaded {len(df)} candles from CSV')
print(df.head())
"

# Test 2: Feature Engineering
python -c "
from src.data_ingestion import ForexDataPipeline
from src.features import engineer_features
pipeline = ForexDataPipeline()
df = pipeline.fetch_historical_data_csv('EURUSD', timeframe=240)
features, engine = engineer_features(df)
print(f'✅ Generated {len(features.columns)} features')
"

# Test 3: Backtester
python -c "
from src.data_ingestion import ForexDataPipeline
from src.features import engineer_features
from src.backtester import Backtester
import numpy as np

pipeline = ForexDataPipeline()
df = pipeline.fetch_historical_data_csv('EURUSD', timeframe=240)
features, _ = engineer_features(df)

rsi = features['rsi_14']
signals = np.zeros(len(df))
signals[rsi < 30] = 1
signals[rsi > 70] = -1

bt = Backtester(df)
bt.backtest(df.reset_index(drop=True), signals)
report = bt.report()

print(f'✅ Backtest complete:')
print(f'   Trades: {report[\"total_trades\"]}')
print(f'   Win Rate: {report[\"win_rate\"]:.1%}')
print(f'   Sharpe: {report[\"sharpe_ratio\"]:.2f}')
print(f'   P&L: \${report[\"total_pnl\"]:.2f}')
"
```

---

## Windows-Specific Tips

### Activate/Deactivate Virtual Environment

```powershell
# Activate (run when starting work)
cd C:\Users\Christian\Desktop\projects\forex-system
.\venv\Scripts\Activate.ps1

# Deactivate (run when done)
deactivate
```

### Edit Files

```powershell
# Edit config
notepad C:\Users\Christian\Desktop\projects\forex-system\config\config.py

# Edit .env
notepad C:\Users\Christian\Desktop\projects\forex-system\.env

# View logs
Get-Content C:\Users\Christian\Desktop\projects\forex-system\logs\forex_system.log -Tail 50
```

### Run Python Scripts

```powershell
# Make sure venv is activated, then:
python src\data_ingestion.py
python src\features.py
python src\backtester.py
python tests\test_phase1.py
```

### Install Additional Packages

```powershell
# If needed for Phase 2:
pip install tensorflow==2.13.0
pip install xgboost==1.7.6
pip install lightgbm==3.3.5
```

---

## Troubleshooting on Windows

### Issue: "python: The term 'python' is not recognized"
**Solution:**
```powershell
# Python not in PATH. Either:
# 1. Reinstall Python and CHECK "Add Python to PATH"
# 2. Use full path: C:\Users\Christian\AppData\Local\Programs\Python\Python311\python.exe
# 3. Use python3 instead: python3 --version
```

### Issue: "venv\Scripts\Activate.ps1 cannot be loaded"
**Solution:**
```powershell
# Fix execution policy:
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Or use batch file:
.\venv\Scripts\activate.bat
```

### Issue: "ModuleNotFoundError: No module named 'psycopg2'"
**Solution:**
```powershell
# Make sure venv is activated, then:
pip install psycopg2-binary

# Or reinstall all:
pip install -r requirements.txt
```

### Issue: "No module named 'MetaTrader5'"
**Solution - This is OK for Phase 1:**
```powershell
# MetaTrader5 is Windows-only (requires MT5 installed)
# For Phase 1, use CSV fallback:
df = pipeline.fetch_historical_data_csv('EURUSD', timeframe=240)

# MT5 connection needed for Phase 3 (live trading)
```

### Issue: Tests fail with database errors
**Solution - This is normal:**
```powershell
# PostgreSQL and Redis optional for Phase 1
# Skip those tests, focus on:
# - Test 1: Imports ✅
# - Test 2: Config ✅
# - Test 3: Data ✅
# - Test 4: Features ✅
# - Test 5: Backtester ✅
# Database tests (6,7) are optional
```

---

## File Checklist

Make sure you have all these files in `C:\Users\Christian\Desktop\projects\forex-system\`:

```
✅ README.md                              (Documentation)
✅ PHASE1_DELIVERY_SUMMARY.md             (What you got)
✅ PHASE1_IMPLEMENTATION_PROMPT.md        (For Claude Code)
✅ requirements.txt                       (Dependencies)

✅ config\config.py                       (Settings)

✅ src\data_ingestion.py                 (Data pipeline)
✅ src\features.py                       (Feature engineering)
✅ src\backtester.py                     (Backtester)

✅ scripts\init_database.py              (DB setup - optional)
✅ scripts\setup_hetzner.sh              (Server setup - optional)

✅ tests\test_phase1.py                  (Tests)

✅ data\EURUSD_240.csv                   (Sample data)

✅ .env                                   (Create this - see Step 7)

✅ logs\                                  (Auto-created when tests run)
```

---

## Quick Command Reference

```powershell
# Navigate to project
cd C:\Users\Christian\Desktop\projects\forex-system

# Activate environment
.\venv\Scripts\Activate.ps1

# Run tests
python tests\test_phase1.py

# Test data loading
python -c "from src.data_ingestion import ForexDataPipeline; pipeline = ForexDataPipeline(); df = pipeline.fetch_historical_data_csv('EURUSD', 240); print(f'Loaded {len(df)} candles')"

# Test features
python -c "from src.features import engineer_features; print('Features OK')"

# Test backtester
python -c "from src.backtester import Backtester; print('Backtester OK')"

# View config
type config\config.py | more

# Edit .env
notepad .env

# View logs
Get-Content logs\forex_system.log -Tail 50

# Deactivate environment
deactivate
```

---

## Next: Phase 2 (After Phase 1 Tests Pass)

Once `python tests\test_phase1.py` shows ✅ success:

1. Copy `PHASE1_IMPLEMENTATION_PROMPT.md` to Claude Code
2. Implement LSTM + XGBoost models (Phase 2)
3. Expected: 4 weeks to production-ready system

---

## Windows + MT5 Note

For **Phase 3 (Live Trading)**, you'll need:

```powershell
# Install MetaTrader5 on Windows
pip install MetaTrader5

# Then in Python:
import MetaTrader5 as mt5
mt5.initialize(
    login=123456789,
    password='your_password',
    server='Exness-MT5'
)
```

MT5 **only works on Windows**. Phase 1 uses CSV, so it's cross-platform.

---

## Support

If you get stuck:

1. Check error message carefully
2. Google the error (99% already solved on Stack Overflow)
3. Review `logs\forex_system.log` for details
4. Check `README.md` troubleshooting section
5. All dependencies listed in `requirements.txt`

---

**You're ready to build the future of algorithmic trading! 🚀**

After files are in place and tests pass → start Phase 2 (ML models)
