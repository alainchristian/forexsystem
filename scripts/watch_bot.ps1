<#
.SYNOPSIS
Live-tails the trading bot's log with order events highlighted.

.PARAMETER OrdersOnly
Hide routine per-symbol prediction noise and only show order events
(opened, closed, blocked, emergency stop, ranked replacement).

.EXAMPLE
.\scripts\watch_bot.ps1

.EXAMPLE
.\scripts\watch_bot.ps1 -OrdersOnly
#>
param(
    [switch]$OrdersOnly
)

$LogFile = "C:\forex-system\logs\forex_system.log"

# Every reason a symbol's signal can fail to become a trade, across
# ensemble.py (confidence filter), main.py (daily data / trend / ATR floor /
# stale data / missing scaler) and mt5_trader.py (risk-manager blocks,
# invalid SL/TP setup, spread, margin, order-send failure). Keep this in
# sync if a new block/skip reason is added to any of those files - a reason
# that isn't in this pattern silently never reaches -OrdersOnly.
$BlockReasons = "Trade blocked|signal blocked|signal skipped|Invalid setup|" +
                "Spread too wide|no spread data|Volume reduced|Low free margin|" +
                "Order failed|ATR invalid|no persisted feature scaler|" +
                "Data too stale|Insufficient data"

function Write-Colorized {
    param([string]$Line)
    if ($Line -match "opened:|closed \(") {
        Write-Host $Line -ForegroundColor Green
    } elseif ($Line -match "$BlockReasons|EMERGENCY") {
        Write-Host $Line -ForegroundColor Yellow
    } elseif ($Line -match "ERROR|CRITICAL") {
        Write-Host $Line -ForegroundColor Red
    } else {
        Write-Host $Line
    }
}

# Get-Content -Wait streams forever and must run directly in the pipeline -
# assigning it to a variable first would block here until end-of-stream,
# which never comes, so nothing would ever print.
if ($OrdersOnly) {
    Get-Content $LogFile -Wait -Tail 20 |
        Where-Object { $_ -match "opened:|closed \(|$BlockReasons|EMERGENCY|Ranked replacement" } |
        ForEach-Object { Write-Colorized $_ }
} else {
    Get-Content $LogFile -Wait -Tail 20 | ForEach-Object { Write-Colorized $_ }
}
