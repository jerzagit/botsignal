param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Python = if (Test-Path "C:\Python314\python.exe") { "C:\Python314\python.exe" } elseif (Test-Path $VenvPython) { $VenvPython } else { "python" }
$DashboardPython = if (Test-Path $VenvPython) { $VenvPython } else { $Python }

function Wait-Docker {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { return }

    $DockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $DockerDesktop) {
        Write-Host "[INFO] Starting Docker Desktop..."
        Start-Process -FilePath $DockerDesktop -WindowStyle Hidden | Out-Null
    }

    for ($i = 1; $i -le 40; $i++) {
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { return }
        Start-Sleep -Seconds 3
    }
    throw "Docker engine is not ready. Start Docker Desktop, then run this script again."
}

function Wait-MySql {
    for ($i = 1; $i -le 30; $i++) {
        $status = docker inspect -f '{{.State.Health.Status}}' botsignal-mysql 2>$null
        if ($status -eq "healthy") { return }
        Start-Sleep -Seconds 3
    }
    throw "MySQL container did not become healthy."
}

function Get-BotProcesses {
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -match '^(python|pythonw|py)\.exe$' -and
            $_.CommandLine -match 'bot\.py'
        }
}

function Get-DashboardProcesses {
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -match '^(python|pythonw|py)\.exe$' -and
            $_.CommandLine -match 'dashboard[\\/]app\.py'
        }
}

function Stop-LocalProcesses {
    Write-Host "[CLEAN] Stopping existing bot/dashboard processes..."

    $bot = Get-BotProcesses
    if ($bot) {
        $bot | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
        Write-Host "[CLEAN] Stopped bot PIDs: $($bot.ProcessId -join ', ')"
    }

    $dash = Get-DashboardProcesses
    if ($dash) {
        $dash | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
        Write-Host "[CLEAN] Stopped dashboard PIDs: $($dash.ProcessId -join ', ')"
    }

    Start-Sleep -Seconds 2
    if (Test-Path "data\bot.pid") { Remove-Item -LiteralPath "data\bot.pid" -Force }
    if (Test-Path "data\startup.timestamp") { Remove-Item -LiteralPath "data\startup.timestamp" -Force }
    if (Test-Path "data\service_locks") {
        Get-ChildItem "data\service_locks" -File | Remove-Item -Force
    }
}

Set-Location $Root
if ($Clean) {
    Stop-LocalProcesses
}

Write-Host "[1/5] Starting database..."
Wait-Docker
docker compose up -d mysql | Out-Host
Wait-MySql
$dbCheck = & $DashboardPython -c "from core.db import is_database_available; print(is_database_available())"
if ($dbCheck -notmatch "True") { throw "Database is not reachable from Python." }
Write-Host "[OK] Database is healthy."

Write-Host "[2/5] Checking MT5..."
$mt5 = Get-Process -Name terminal64 -ErrorAction SilentlyContinue
if (-not $mt5) {
    throw "MT5 is not open. Open MT5 manually, login, enable AutoTrading, then run this script again."
}
$mt5Check = & $DashboardPython -c "from core.mt5 import mt5_connect_test, mt5_disconnect; ok,msg=mt5_connect_test(); print('OK' if ok else 'FAIL'); print(msg); mt5_disconnect()"
if ($mt5Check[0] -ne "OK") {
    $mt5Check | ForEach-Object { Write-Host $_ }
    throw "MT5 preflight failed. Fix MT5 login/AutoTrading, then run this script again."
}
Write-Host "[OK] MT5 preflight passed."

Write-Host "[3/5] Starting bot..."
$bot = Get-BotProcesses
if ($bot) {
    Write-Host "[SKIP] Bot already running:"
    $bot | Select-Object ProcessId,ParentProcessId,CommandLine | Format-Table -AutoSize
} else {
    Start-Process -FilePath $Python -ArgumentList "bot.py" -WorkingDirectory $Root -WindowStyle Hidden -PassThru | Out-Null
    Start-Sleep -Seconds 5
    $bot = Get-BotProcesses
    if (($bot | Measure-Object).Count -ne 1) {
        $bot | Select-Object ProcessId,ParentProcessId,CommandLine | Format-Table -AutoSize
        throw "Expected exactly one bot process after startup."
    }
    Write-Host "[OK] Bot started with PID $($bot.ProcessId)."
}

Write-Host "[4/5] Starting dashboard..."
$dash = Get-DashboardProcesses
if ($dash) {
    Write-Host "[SKIP] Dashboard already running:"
    $dash | Select-Object ProcessId,ParentProcessId,CommandLine | Format-Table -AutoSize
} else {
    Start-Process -FilePath $DashboardPython -ArgumentList "dashboard/app.py" -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput "dash_out_service.log" -RedirectStandardError "dash_err_service.log" -PassThru | Out-Null
    Start-Sleep -Seconds 5
    $dashPort = Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
    if (-not $dashPort) { throw "Dashboard did not open port 5000. Check dash_err_service.log." }
    Write-Host "[OK] Dashboard is listening on http://127.0.0.1:5000"
}

Write-Host "[5/5] Stack status:"
& (Join-Path $Root "status_project.ps1")
