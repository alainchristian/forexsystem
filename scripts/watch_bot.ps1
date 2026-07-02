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

function Write-Colorized {
    param([string]$Line)
    if ($Line -match "opened:|closed \(") {
        Write-Host $Line -ForegroundColor Green
    } elseif ($Line -match "Trade blocked|blocked by confidence|signal skipped|EMERGENCY") {
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
        Where-Object { $_ -match "opened:|closed \(|Trade blocked|EMERGENCY|Ranked replacement" } |
        ForEach-Object { Write-Colorized $_ }
} else {
    Get-Content $LogFile -Wait -Tail 20 | ForEach-Object { Write-Colorized $_ }
}
