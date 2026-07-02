# =============================================================================
# Forex Trading System - Non-interactive VPS Setup
# Credentials pre-filled from .env
# =============================================================================

$ErrorActionPreference = "Stop"
$PROJECT_PATH   = "C:\forex-system"
$PYTHON_VERSION = "3.11.9"
$PG_VERSION     = "15.7"
$DB_NAME        = "forex_trading_db"
$DB_USER        = "admin"
$DB_PASSWORD    = "admin"

# Postgres superuser password is read from the environment rather than
# hardcoded, so it never ends up in git history. Set it before running:
#   $env:PG_SUPERPASS = "..."; .\setup_vps_auto.ps1
if (-not $env:PG_SUPERPASS) {
    Write-Host "ERROR: `$env:PG_SUPERPASS is not set. Run:" -ForegroundColor Red
    Write-Host '  $env:PG_SUPERPASS = "your-postgres-superuser-password"' -ForegroundColor Red
    exit 1
}
$PG_SUPERPASS = $env:PG_SUPERPASS

function Write-Step($n, $msg) {
    Write-Host ""
    Write-Host "[$n] $msg" -ForegroundColor Cyan
}
function Write-OK($msg)   { Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  WARN  $msg" -ForegroundColor Yellow }

# Admin check
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]"Administrator")) {
    Write-Host "ERROR: Run this script as Administrator." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Yellow
Write-Host "  FOREX TRADING SYSTEM - VPS SETUP (AUTO)" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Yellow

# Step 1: Project directories
Write-Step 1 "Creating project directories"
foreach ($dir in @("logs","data","models","config","src","scripts","tests")) {
    New-Item -ItemType Directory -Path "$PROJECT_PATH\$dir" -Force | Out-Null
}
Write-OK "Directories ready"

# Step 2: Install Python
Write-Step 2 "Installing Python $PYTHON_VERSION"
$pythonCheck = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCheck) {
    Write-OK "Python already installed"
} else {
    $pyInstaller = "$env:TEMP\python-$PYTHON_VERSION-amd64.exe"
    Write-Host "  Downloading Python (~25 MB)..." -ForegroundColor Gray
    Invoke-WebRequest "https://www.python.org/ftp/python/$PYTHON_VERSION/python-$PYTHON_VERSION-amd64.exe" -OutFile $pyInstaller
    Start-Process $pyInstaller -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1" -Wait
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")
    Write-OK "Python installed"
}

# Step 3: Install Git
Write-Step 3 "Installing Git"
$gitCheck = Get-Command git -ErrorAction SilentlyContinue
if ($gitCheck) {
    Write-OK "Git already installed"
} else {
    $gitInstaller = "$env:TEMP\git-installer.exe"
    Write-Host "  Downloading Git (~60 MB)..." -ForegroundColor Gray
    Invoke-WebRequest "https://github.com/git-for-windows/git/releases/download/v2.45.2.windows.1/Git-2.45.2-64-bit.exe" -OutFile $gitInstaller
    Start-Process $gitInstaller -ArgumentList "/VERYSILENT /NORESTART" -Wait
    $env:PATH += ";C:\Program Files\Git\cmd"
    Write-OK "Git installed"
}

# Step 4: Install PostgreSQL
Write-Step 4 "Installing PostgreSQL $PG_VERSION"
$pgCheck = Get-Service -Name "postgresql*" -ErrorAction SilentlyContinue
if ($pgCheck) {
    Write-OK "PostgreSQL service already exists"
} else {
    $pgInstaller = "$env:TEMP\pg-installer.exe"
    Write-Host "  Downloading PostgreSQL (~200 MB)..." -ForegroundColor Gray
    Invoke-WebRequest "https://get.enterprisedb.com/postgresql/postgresql-$PG_VERSION-1-windows-x64.exe" -OutFile $pgInstaller
    Write-Host "  Installing PostgreSQL (this takes ~2 minutes)..." -ForegroundColor Gray
    Start-Process $pgInstaller -ArgumentList "--unattendedmodeui none --mode unattended --superpassword `"$PG_SUPERPASS`" --serverport 5432" -Wait
    $env:PATH += ";C:\Program Files\PostgreSQL\$($PG_VERSION.Split('.')[0])\bin"
    Write-OK "PostgreSQL installed"

    Write-Host "  Creating database and user..." -ForegroundColor Gray
    $pgBin = "C:\Program Files\PostgreSQL\$($PG_VERSION.Split('.')[0])\bin"
    $env:PGPASSWORD = $PG_SUPERPASS
    & "$pgBin\psql.exe" -U postgres -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';" 2>$null
    & "$pgBin\psql.exe" -U postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" 2>$null
    & "$pgBin\psql.exe" -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;" 2>$null
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    Write-OK "Database '$DB_NAME' and user '$DB_USER' created"
}

# Step 5: Install Redis via Chocolatey
Write-Step 5 "Installing Redis"
$redisCheck = Get-Service -Name "redis*" -ErrorAction SilentlyContinue
if ($redisCheck) {
    Write-OK "Redis service already exists"
} else {
    $chocoCheck = Get-Command choco -ErrorAction SilentlyContinue
    if (-not $chocoCheck) {
        Write-Host "  Installing Chocolatey..." -ForegroundColor Gray
        Set-ExecutionPolicy Bypass -Scope Process -Force
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
        Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
        $env:PATH += ";$env:ALLUSERSPROFILE\chocolatey\bin"
        Write-OK "Chocolatey installed"
    } else {
        Write-OK "Chocolatey already installed"
    }
    Write-Host "  Installing Redis via Chocolatey..." -ForegroundColor Gray
    choco install redis-64 -y --no-progress
    $env:PATH += ";C:\tools\redis"
    try {
        Start-Service redis -ErrorAction SilentlyContinue
        Set-Service -Name redis -StartupType Automatic -ErrorAction SilentlyContinue
        Write-OK "Redis installed and running as a service"
    } catch {
        Write-Warn "Redis service not registered - starting redis-server.exe manually"
        Start-Process "C:\tools\redis\redis-server.exe" -WindowStyle Hidden
    }
}

# Step 6: Python venv and dependencies
Write-Step 6 "Setting up Python virtual environment"
Set-Location $PROJECT_PATH

if (-not (Test-Path "$PROJECT_PATH\venv")) {
    python -m venv venv
    Write-OK "Virtual environment created"
} else {
    Write-OK "Virtual environment already exists"
}

Write-Host "  Upgrading pip..." -ForegroundColor Gray
& "$PROJECT_PATH\venv\Scripts\python.exe" -m pip install --upgrade pip --quiet

Write-Host "  Installing dependencies (may take 5-10 min)..." -ForegroundColor Gray
& "$PROJECT_PATH\venv\Scripts\pip.exe" install -r "$PROJECT_PATH\requirements.txt"
Write-OK "Dependencies installed"

# Step 7: Check .env
Write-Step 7 "Checking .env configuration file"
if (Test-Path "$PROJECT_PATH\.env") {
    Write-OK ".env already exists - skipping"
} else {
    Write-Warn ".env not found - create it manually at $PROJECT_PATH\.env"
}

# Step 8: Register scheduled task
Write-Step 8 "Registering bot as scheduled task (auto-start on boot)"
$taskName = "ForexTradingBot"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Warn "Task '$taskName' already registered - skipping"
} else {
    $action = New-ScheduledTaskAction `
        -Execute "$PROJECT_PATH\venv\Scripts\python.exe" `
        -Argument "$PROJECT_PATH\src\main.py" `
        -WorkingDirectory $PROJECT_PATH
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Days 365)
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -RunLevel Highest `
        -Force | Out-Null
    Write-OK "Scheduled task '$taskName' registered"
}

# Done
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  SETUP COMPLETE" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
Write-Host "  1. Install and log in to MetaTrader 5 (leave it running)"
Write-Host "  2. Bootstrap historical data:"
Write-Host "     cd $PROJECT_PATH"
Write-Host "     .\venv\Scripts\Activate.ps1"
Write-Host "     python src\data_ingestion.py"
Write-Host "  3. Start the bot:"
Write-Host "     python src\main.py"
Write-Host ""
Write-Host "Logs:   $PROJECT_PATH\logs\" -ForegroundColor Gray
Write-Host "Config: $PROJECT_PATH\.env" -ForegroundColor Gray
Write-Host ""
