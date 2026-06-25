@echo off
REM ============================================================================
REM Forex Trading System - Windows Setup Script
REM Usage: Right-click setup_windows.bat, Run as Administrator
REM Target: C:\Users\Christian\Desktop\projects\forex-system
REM ============================================================================

setlocal enabledelayedexpansion
color 0A
cls

echo.
echo ============================================================================
echo   FOREX TRADING SYSTEM - WINDOWS SETUP
echo ============================================================================
echo.

REM Check if running as admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Please run this script as Administrator!
    echo Right-click setup_windows.bat ^> Run as Administrator
    pause
    exit /b 1
)

REM Set project path
set "PROJECT_PATH=C:\Users\Christian\Desktop\projects\forex-system"

echo [1/6] Creating directory structure...
if not exist "%PROJECT_PATH%" (
    mkdir "%PROJECT_PATH%"
    echo ✓ Created %PROJECT_PATH%
) else (
    echo ✓ Directory already exists
)

cd /d "%PROJECT_PATH%"

mkdir logs 2>nul
mkdir data 2>nul
mkdir config 2>nul
mkdir src 2>nul
mkdir scripts 2>nul
mkdir tests 2>nul
mkdir models 2>nul

echo.
echo [2/6] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found!
    echo Download from: https://www.python.org/downloads/
    echo Make sure to CHECK "Add Python to PATH" during installation
    pause
    exit /b 1
) else (
    python --version
    echo ✓ Python found
)

echo.
echo [3/6] Creating virtual environment...
if not exist "venv" (
    python -m venv venv
    echo ✓ Virtual environment created
) else (
    echo ✓ Virtual environment already exists
)

echo.
echo [4/6] Activating virtual environment...
call venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo ERROR: Failed to activate virtual environment
    pause
    exit /b 1
) else (
    echo ✓ Virtual environment activated
)

echo.
echo [5/6] Installing dependencies...
python -m pip install --upgrade pip >nul 2>&1
pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo WARNING: Some dependencies may have failed
    echo Check output above for details
) else (
    echo ✓ Dependencies installed
)

echo.
echo [6/6] Creating .env file...
if not exist ".env" (
    (
        echo # PostgreSQL
        echo FOREX_DB_NAME=forex_trading_db
        echo FOREX_DB_USER=claude
        echo FOREX_DB_PASSWORD=change_me_in_production
        echo FOREX_DB_HOST=localhost
        echo FOREX_DB_PORT=5432
        echo.
        echo # Redis
        echo REDIS_HOST=localhost
        echo REDIS_PORT=6379
        echo.
        echo # MT5
        echo MT5_ACCOUNT=0
        echo MT5_PASSWORD=
        echo MT5_SERVER=Exness-MT5
    ) > .env
    echo ✓ .env file created
    echo   Edit with: notepad .env
) else (
    echo ✓ .env file already exists
)

echo.
echo ============================================================================
echo   ✓ SETUP COMPLETE!
echo ============================================================================
echo.
echo Next steps:
echo.
echo 1. Copy all source files to: %PROJECT_PATH%
echo    - src\*.py
echo    - config\*.py
echo    - scripts\*.py
echo    - tests\*.py
echo    - data\*.csv
echo    - *.md files
echo.
echo 2. Run tests:
echo    Open PowerShell in %PROJECT_PATH%
echo    Run: .\venv\Scripts\Activate.ps1
echo    Then: python tests\test_phase1.py
echo.
echo 3. Verify files:
echo    dir /s
echo.
echo 4. Edit configuration:
echo    notepad .env
echo.
echo For detailed instructions, see: WINDOWS_SETUP_GUIDE.md
echo.
pause
