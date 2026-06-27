# ============================================================================
# Forex Trading System - VPS Setup Script
# Run in PowerShell as Administrator on the Contabo Windows VPS
# Usage: .\setup_vps.ps1
# ============================================================================

$ErrorActionPreference = "Stop"
$PROJECT_PATH = "C:\forex-system"
$PYTHON_VERSION = "3.11.9"
$PG_VERSION = "15.7"
$DB_NAME = "forex_trading_db"
$DB_USER = "forex_user"

function Write-Step($n, $msg) {
    Write-Host ""
    Write-Host "[$n] $msg" -ForegroundColor Cyan
}

function Write-OK($msg) {
    Write-Host "  OK  $msg" -ForegroundColor Green
}

function Write-Warn($msg) {
    Write-Host "  WARN  $msg" -ForegroundColor Yellow
}

# ── Admin check ──────────────────────────────────────────────────────────────
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]"Administrator")) {
    Write-Host "ERROR: Run this script as Administrator." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Yellow
Write-Host "  FOREX TRADING SYSTEM - VPS SETUP" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Yellow

# ── Step 1: Create project directory ─────────────────────────────────────────
Write-Step 1 "Creating project directory at $PROJECT_PATH"
if (-not (Test-Path $PROJECT_PATH)) {
    New-Item -ItemType Directory -Path $PROJECT_PATH | Out-Null
}
foreach ($dir in @("logs","data","models","config","src","scripts","tests")) {
    New-Item -ItemType Directory -Path "$PROJECT_PATH\$dir" -Force | Out-Null
}
Write-OK "Directories ready"

# ── Step 2: Install Python ────────────────────────────────────────────────────
Write-Step 2 "Installing Python $PYTHON_VERSION"
$pythonCheck = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCheck) {
    Write-OK "Python already installed: $(python --version)"
} else {
    $pyInstaller = "$env:TEMP\python-$PYTHON_VERSION-amd64.exe"
    Write-Host "  Downloading Python..." -ForegroundColor Gray
    Invoke-WebRequest "https://www.python.org/ftp/python/$PYTHON_VERSION/python-$PYTHON_VERSION-amd64.exe" -OutFile $pyInstaller
    Start-Process $pyInstaller -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1" -Wait
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")
    Write-OK "Python installed"
}

# ── Step 3: Install Git ───────────────────────────────────────────────────────
Write-Step 3 "Installing Git"
$gitCheck = Get-Command git -ErrorAction SilentlyContinue
if ($gitCheck) {
    Write-OK "Git already installed"
} else {
    $gitInstaller = "$env:TEMP\git-installer.exe"
    Write-Host "  Downloading Git..." -ForegroundColor Gray
    Invoke-WebRequest "https://github.com/git-for-windows/git/releases/download/v2.45.2.windows.1/Git-2.45.2-64-bit.exe" -OutFile $gitInstaller
    Start-Process $gitInstaller -ArgumentList "/VERYSILENT /NORESTART" -Wait
    $env:PATH += ";C:\Program Files\Git\cmd"
    Write-OK "Git installed"
}

# ── Step 4: Install PostgreSQL ────────────────────────────────────────────────
Write-Step 4 "Installing PostgreSQL $PG_VERSION"
$pgCheck = Get-Service -Name "postgresql*" -ErrorAction SilentlyContinue
if ($pgCheck) {
    Write-OK "PostgreSQL service already exists"
} else {
    $pgInstaller = "$env:TEMP\pg-installer.exe"
    Write-Host "  Downloading PostgreSQL..." -ForegroundColor Gray
    Invoke-WebRequest "https://get.enterprisedb.com/postgresql/postgresql-$PG_VERSION-1-windows-x64.exe" -OutFile $pgInstaller

    # Prompt for DB password
    Write-Host ""
    $pgPassword = Read-Host "  Enter a password for the PostgreSQL 'postgres' superuser"
    $dbPassword = Read-Host "  Enter a password for the '$DB_USER' database user (used in .env)"

    Start-Process $pgInstaller -ArgumentList "--unattendedmodeui none --mode unattended --superpassword `"$pgPassword`" --serverport 5432" -Wait
    $env:PATH += ";C:\Program Files\PostgreSQL\$($PG_VERSION.Split('.')[0])\bin"
    Write-OK "PostgreSQL installed"

    # Create DB and user
    Write-Host "  Creating database and user..." -ForegroundColor Gray
    $pgBin = "C:\Program Files\PostgreSQL\$($PG_VERSION.Split('.')[0])\bin"
    $env:PGPASSWORD = $pgPassword
    & "$pgBin\psql.exe" -U postgres -c "CREATE USER $DB_USER WITH PASSWORD '$dbPassword';" 2>$null
    & "$pgBin\psql.exe" -U postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" 2>$null
    & "$pgBin\psql.exe" -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;" 2>$null
    Remove-Item Env:\PGPASSWORD
    Write-OK "Database '$DB_NAME' and user '$DB_USER' created"
} else {
    $dbPassword = Read-Host "  PostgreSQL already installed. Enter password for '$DB_USER' (for .env file)"
}

# ── Step 5: Copy project files ────────────────────────────────────────────────
Write-Step 5 "Project files"
Write-Host ""
Write-Host "  Place your project zip on the Desktop, then press Enter." -ForegroundColor Yellow
Write-Host "  Expected zip name: forex-system.zip" -ForegroundColor Yellow
Write-Host "  (Or press S to skip if files are already at $PROJECT_PATH)" -ForegroundColor Yellow
$choice = Read-Host "  [Enter to extract / S to skip]"

if ($choice -ne "S" -and $choice -ne "s") {
    $zipPath = "$env:USERPROFILE\Desktop\forex-system.zip"
    if (Test-Path $zipPath) {
        Expand-Archive -Path $zipPath -DestinationPath "C:\" -Force
        # Handle nested folder from zip
        if (Test-Path "C:\forex-system\forex-system") {
            Get-ChildItem "C:\forex-system\forex-system" | Move-Item -Destination "C:\forex-system" -Force
            Remove-Item "C:\forex-system\forex-system" -Force
        }
        Write-OK "Project extracted to $PROJECT_PATH"
    } else {
        Write-Warn "forex-system.zip not found on Desktop — skipping extraction"
    }
}

# ── Step 6: Python virtual environment & dependencies ────────────────────────
Write-Step 6 "Setting up Python virtual environment"
Set-Location $PROJECT_PATH

if (-not (Test-Path "$PROJECT_PATH\venv")) {
    python -m venv venv
    Write-OK "Virtual environment created"
} else {
    Write-OK "Virtual environment already exists"
}

Write-Host "  Installing Python dependencies (this takes a few minutes)..." -ForegroundColor Gray
& "$PROJECT_PATH\venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
& "$PROJECT_PATH\venv\Scripts\pip.exe" install -r "$PROJECT_PATH\requirements.txt"
Write-OK "Dependencies installed"

# ── Step 7: Create .env file ──────────────────────────────────────────────────
Write-Step 7 "Creating .env configuration file"

if (-not (Test-Path "$PROJECT_PATH\.env")) {
    Write-Host ""
    $mt5Account  = Read-Host "  MT5 account number"
    $mt5Password = Read-Host "  MT5 password"
    $mt5Server   = Read-Host "  MT5 server (e.g. Exness-MT5Real)"
    $tgToken     = Read-Host "  Telegram bot token (or press Enter to skip)"
    $tgChatId    = Read-Host "  Telegram chat ID (or press Enter to skip)"

    $envContent = @"
# PostgreSQL
FOREX_DB_NAME=$DB_NAME
FOREX_DB_USER=$DB_USER
FOREX_DB_PASSWORD=$dbPassword
FOREX_DB_HOST=localhost
FOREX_DB_PORT=5432

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# MetaTrader 5
MT5_ACCOUNT=$mt5Account
MT5_PASSWORD=$mt5Password
MT5_SERVER=$mt5Server

# Telegram
TELEGRAM_BOT_TOKEN=$tgToken
TELEGRAM_CHAT_ID=$tgChatId
"@
    $envContent | Out-File -FilePath "$PROJECT_PATH\.env" -Encoding utf8
    Write-OK ".env file created"
} else {
    Write-Warn ".env already exists — skipped. Edit manually: notepad $PROJECT_PATH\.env"
}

# ── Step 8: Register bot as Windows Task (auto-start on boot) ────────────────
Write-Step 8 "Registering forex bot as a scheduled task (auto-start on boot)"

$taskName = "ForexTradingBot"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Warn "Task '$taskName' already registered — skipping"
} else {
    $action  = New-ScheduledTaskAction `
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

    Write-OK "Scheduled task '$taskName' registered — bot will start automatically on reboot"
}

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  SETUP COMPLETE" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
Write-Host "  1. Install & log in to MetaTrader 5 terminal (leave it running)"
Write-Host "  2. Train models:"
Write-Host "     cd $PROJECT_PATH"
Write-Host "     .\venv\Scripts\Activate.ps1"
Write-Host "     python src\train_models.py"
Write-Host "  3. Start the bot:"
Write-Host "     python src\main.py"
Write-Host "  4. Or reboot — the scheduled task will start it automatically"
Write-Host ""
Write-Host "Logs: $PROJECT_PATH\logs\" -ForegroundColor Gray
Write-Host "Config: $PROJECT_PATH\.env" -ForegroundColor Gray
Write-Host ""
