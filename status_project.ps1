$ErrorActionPreference = "Continue"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host ""
Write-Host "SignalBot Project Status"
Write-Host "------------------------"

Write-Host ""
Write-Host "Database:"
docker compose ps mysql

Write-Host ""
Write-Host "Bot process:"
$bot = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match '^(python|pythonw|py)\.exe$' -and
        $_.CommandLine -match 'bot\.py'
    }
if ($bot) {
    $bot | Select-Object ProcessId,ParentProcessId,CommandLine | Format-Table -AutoSize
} else {
    Write-Host "Not running"
}

Write-Host ""
Write-Host "Dashboard process:"
$dash = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match '^(python|pythonw|py)\.exe$' -and
        $_.CommandLine -match 'dashboard[\\/]app\.py'
    }
if ($dash) {
    $dash | Select-Object ProcessId,ParentProcessId,CommandLine | Format-Table -AutoSize
} else {
    Write-Host "Not running"
}

Write-Host ""
Write-Host "Dashboard port:"
$port = Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
if ($port) {
    $port | Select-Object LocalAddress,LocalPort,State,OwningProcess | Format-Table -AutoSize
    Write-Host "URL: http://127.0.0.1:5000"
} else {
    Write-Host "Not listening"
}

Write-Host ""
Write-Host "MT5 terminal:"
$mt5 = Get-Process -Name terminal64 -ErrorAction SilentlyContinue
if ($mt5) {
    $mt5 | Select-Object Id,ProcessName,StartTime | Format-Table -AutoSize
} else {
    Write-Host "Not running"
}
