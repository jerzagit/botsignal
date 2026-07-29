param(
    [switch]$StopDatabase,
    [switch]$StopMT5
)

$ErrorActionPreference = "Continue"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "[1/3] Stopping bot..."
$bot = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match '^(python|pythonw|py)\.exe$' -and
        $_.CommandLine -match 'bot\.py'
    }
if ($bot) {
    $bot | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Write-Host "[OK] Stopped bot PIDs: $($bot.ProcessId -join ', ')"
} else {
    Write-Host "[OK] Bot was not running."
}

Write-Host "[2/3] Stopping dashboard..."
$dash = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match '^(python|pythonw|py)\.exe$' -and
        $_.CommandLine -match 'dashboard[\\/]app\.py'
    }
if ($dash) {
    $dash | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Write-Host "[OK] Stopped dashboard PIDs: $($dash.ProcessId -join ', ')"
} else {
    Write-Host "[OK] Dashboard was not running."
}

Write-Host "[3/3] Optional services..."
if ($StopDatabase) {
    docker compose stop mysql | Out-Host
    Write-Host "[OK] Database stopped."
} else {
    Write-Host "[SKIP] Database left running. Use -StopDatabase to stop it."
}

if ($StopMT5) {
    Stop-Process -Name terminal64 -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] MT5 stop requested."
} else {
    Write-Host "[SKIP] MT5 left open. Use -StopMT5 to close it."
}

if (Test-Path "data\bot.pid") { Remove-Item -LiteralPath "data\bot.pid" -Force }
if (Test-Path "data\startup.timestamp") { Remove-Item -LiteralPath "data\startup.timestamp" -Force }
if (Test-Path "data\service_locks") {
    Get-ChildItem "data\service_locks" -File | Remove-Item -Force
}

Write-Host ""
& (Join-Path $Root "status_project.ps1")
