# install_services.ps1
# Registers SignalBot and Dashboard as Windows services using NSSM.
# Services auto-start on boot and auto-restart on crash.
#
# Run as Administrator after setup_vps.ps1 completes.

$ErrorActionPreference = "Stop"
$ProjectDir  = "C:\signalbot"
$PythonExe   = (Get-Command python).Source
$NssmExe     = "nssm"
$LogDir      = "$ProjectDir\logs"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Installing SignalBot Windows Services" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ── Helper ────────────────────────────────────────────────────────────────────
function Install-BotService {
    param($Name, $Script, $LogFile)

    Write-Host "Installing service: $Name" -ForegroundColor Yellow

    # Remove existing service if any
    $existing = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($existing) {
        & $NssmExe stop $Name confirm | Out-Null
        & $NssmExe remove $Name confirm | Out-Null
        Write-Host "  Removed existing service." -ForegroundColor Gray
    }

    # Install
    & $NssmExe install $Name $PythonExe $Script
    & $NssmExe set $Name AppDirectory $ProjectDir
    & $NssmExe set $Name AppStdout "$LogDir\$LogFile"
    & $NssmExe set $Name AppStderr "$LogDir\$LogFile"
    & $NssmExe set $Name AppRotateFiles 1
    & $NssmExe set $Name AppRotateBytes 10485760   # rotate at 10 MB
    & $NssmExe set $Name Start SERVICE_AUTO_START
    & $NssmExe set $Name AppRestartDelay 5000       # restart after 5s on crash

    Write-Host "  Installed: $Name" -ForegroundColor Green
}

# ── Install services ──────────────────────────────────────────────────────────
Install-BotService -Name "SignalBot"          -Script "$ProjectDir\bot.py"            -LogFile "bot.log"
Install-BotService -Name "SignalBotDashboard" -Script "$ProjectDir\dashboard\app.py"  -LogFile "dashboard.log"

# ── Start services ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Starting services..." -ForegroundColor Yellow
Start-Service -Name "SignalBot"
Start-Service -Name "SignalBotDashboard"

Start-Sleep -Seconds 3

$botStatus  = (Get-Service -Name "SignalBot").Status
$dashStatus = (Get-Service -Name "SignalBotDashboard").Status

Write-Host ""
Write-Host "SignalBot          : $botStatus" -ForegroundColor $(if ($botStatus -eq "Running") {"Green"} else {"Red"})
Write-Host "SignalBotDashboard : $dashStatus" -ForegroundColor $(if ($dashStatus -eq "Running") {"Green"} else {"Red"})
Write-Host ""
Write-Host "Service commands:" -ForegroundColor Cyan
Write-Host "  Start  : Start-Service SignalBot"
Write-Host "  Stop   : Stop-Service SignalBot"
Write-Host "  Restart: Restart-Service SignalBot"
Write-Host "  Status : Get-Service SignalBot"
Write-Host ""
Write-Host "Dashboard : http://localhost:5000  (or http://YOUR-VPS-IP:5000)"
Write-Host ""
