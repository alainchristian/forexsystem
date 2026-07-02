# Weekly model retraining wrapper, invoked by the "ForexModelRetrain"
# scheduled task (Sundays 16:00). Runs train_models.py, which saves a new
# timestamped version under models/versions/ and promotes it to the live
# models/ path on success — it does NOT restart the trading bot, so the
# running process keeps using whatever it already loaded until someone
# restarts it deliberately after reviewing `--list-versions`.

$ErrorActionPreference = "Stop"
$ProjectPath = "C:\forex-system"
$LogFile = Join-Path $ProjectPath "logs\train_models.log"

Set-Location $ProjectPath

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $LogFile -Value "`n===== Retrain run started $timestamp ====="

& "$ProjectPath\venv\Scripts\python.exe" "$ProjectPath\src\train_models.py" *>> $LogFile
$exitCode = $LASTEXITCODE

$finished = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $LogFile -Value "===== Retrain run finished $finished (exit $exitCode) ====="
exit $exitCode
