# FILES TO DOWNLOAD & COPY - WINDOWS SETUP

## Download Location
All files are in Claude outputs. Download them to your computer first.

## Target Directory
```
C:\Users\Christian\Desktop\projects\forex-system\
```

---

## FILE CHECKLIST - Copy these to the directory above

### 📄 ROOT LEVEL FILES (Copy to C:\Users\Christian\Desktop\projects\forex-system\)

```
✅ README.md
✅ PHASE1_DELIVERY_SUMMARY.md
✅ PHASE1_IMPLEMENTATION_PROMPT.md
✅ requirements.txt
✅ setup_windows.bat                    ← Run this first!
✅ WINDOWS_SETUP_GUIDE.md
✅ .env                                 ← Create with editor (see below)
```

### 📁 CONFIG FOLDER
```
Create: C:\Users\Christian\Desktop\projects\forex-system\config\

✅ config.py
```

### 📁 SRC FOLDER (Core Code)
```
Create: C:\Users\Christian\Desktop\projects\forex-system\src\

✅ data_ingestion.py                    (520 lines)
✅ features.py                          (650 lines)
✅ backtester.py                        (650 lines)
```

### 📁 SCRIPTS FOLDER
```
Create: C:\Users\Christian\Desktop\projects\forex-system\scripts\

✅ init_database.py                     (Optional - PostgreSQL setup)
✅ setup_hetzner.sh                     (Optional - Server setup)
```

### 📁 TESTS FOLDER
```
Create: C:\Users\Christian\Desktop\projects\forex-system\tests\

✅ test_phase1.py                       (420 lines)
```

### 📁 DATA FOLDER
```
Create: C:\Users\Christian\Desktop\projects\forex-system\data\

✅ EURUSD_240.csv                       (Sample data for testing)
```

### 📁 LOGS FOLDER (Auto-Created)
```
Create: C:\Users\Christian\Desktop\projects\forex-system\logs\

This folder is created automatically when tests run.
```

### 📁 MODELS FOLDER (For Phase 2)
```
Create: C:\Users\Christian\Desktop\projects\forex-system\models\

Empty for now - will hold trained ML models in Phase 2.
```

---

## SETUP INSTRUCTIONS

### STEP 1: Create .env File

Open Notepad and paste this:

```
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
```

Save as: `C:\Users\Christian\Desktop\projects\forex-system\.env`

### STEP 2: Run setup_windows.bat

1. Right-click `setup_windows.bat`
2. Click "Run as Administrator"
3. Wait for it to complete
4. It will create venv and install dependencies

### STEP 3: Copy All Source Files

Copy all files from the checklist above to their respective folders:

```
C:\Users\Christian\Desktop\projects\forex-system\
├── README.md
├── requirements.txt
├── setup_windows.bat
├── .env
├── config\config.py
├── src\data_ingestion.py
├── src\features.py
├── src\backtester.py
├── tests\test_phase1.py
├── data\EURUSD_240.csv
├── logs\                              (auto-created)
└── models\                            (for Phase 2)
```

### STEP 4: Verify Structure

Open PowerShell in the project directory:

```powershell
cd C:\Users\Christian\Desktop\projects\forex-system
dir /s /b
```

You should see all the files listed above.

### STEP 5: Run Tests

```powershell
# Activate virtual environment
.\venv\Scripts\Activate.ps1

# Run tests
python tests\test_phase1.py
```

Expected: ✅ 7+ tests pass

---

## FILE SIZES (Approximate)

| File | Size | Type |
|------|------|------|
| config.py | 8 KB | Python |
| data_ingestion.py | 20 KB | Python |
| features.py | 25 KB | Python |
| backtester.py | 25 KB | Python |
| test_phase1.py | 16 KB | Python |
| EURUSD_240.csv | 4 KB | Data |
| README.md | 25 KB | Documentation |
| requirements.txt | 1 KB | Text |
| **Total** | **~130 KB** | - |

**Very small! Easy to download and copy.**

---

## DOWNLOAD CHECKLIST

Before starting setup, make sure you have:

```
☐ Downloaded all files from Claude outputs
☐ Created C:\Users\Christian\Desktop\projects\ directory
☐ Have Python 3.11+ installed (python --version)
☐ Running as Administrator
☐ Notepad or editor ready for .env file
```

---

## QUICK START AFTER FILES ARE IN PLACE

```powershell
# 1. Navigate to project
cd C:\Users\Christian\Desktop\projects\forex-system

# 2. Activate environment
.\venv\Scripts\Activate.ps1

# 3. Run tests
python tests\test_phase1.py

# 4. Should see: ✅ All tests passed!
```

---

## TROUBLESHOOTING

**Issue**: "File not found" error
```
Solution: Make sure all files are in correct folders
Check with: dir C:\Users\Christian\Desktop\projects\forex-system\src\
```

**Issue**: "ModuleNotFoundError"
```
Solution: Virtual environment not activated
Run: .\venv\Scripts\Activate.ps1
You should see (venv) at start of PowerShell prompt
```

**Issue**: "Python not found"
```
Solution: Python not in PATH
- Reinstall Python
- CHECK "Add Python to PATH" option
- Or use full path: C:\Users\...\Python311\python.exe
```

---

## FILE ORGANIZATION DIAGRAM

```
C:\Users\Christian\
└── Desktop\
    └── projects\
        └── forex-system\               ← PROJECT ROOT
            ├── README.md               ← Start here
            ├── requirements.txt        ← Dependencies list
            ├── setup_windows.bat       ← Run as admin
            ├── .env                    ← Configuration (create)
            │
            ├── config\
            │   └── config.py
            │
            ├── src\                    ← Core code
            │   ├── data_ingestion.py
            │   ├── features.py
            │   └── backtester.py
            │
            ├── tests\                  ← Validation
            │   └── test_phase1.py
            │
            ├── scripts\                ← Automation
            │   ├── init_database.py
            │   └── setup_hetzner.sh
            │
            ├── data\                   ← Sample data
            │   └── EURUSD_240.csv
            │
            ├── logs\                   ← Auto-created
            │   └── (empty initially)
            │
            ├── models\                 ← For Phase 2
            │   └── (empty initially)
            │
            └── venv\                   ← Virtual environment
                ├── Scripts\            ← Created by setup
                └── Lib\
```

---

## NEXT STEPS AFTER SETUP

1. ✅ All files copied
2. ✅ setup_windows.bat ran successfully
3. ✅ python tests\test_phase1.py passes

Then:

4. Copy `PHASE1_IMPLEMENTATION_PROMPT.md` to Claude Code
5. Implement Phase 2 (LSTM + XGBoost models)
6. Estimated: 4 weeks to production

---

## REFERENCES

- **Full Setup Guide**: WINDOWS_SETUP_GUIDE.md
- **Implementation Steps**: PHASE1_IMPLEMENTATION_PROMPT.md
- **What You Got**: PHASE1_DELIVERY_SUMMARY.md
- **Complete Docs**: README.md

---

**All set? Let's build! 🚀**
