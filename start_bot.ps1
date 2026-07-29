$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = if (Test-Path "C:\Python314\python.exe") { "C:\Python314\python.exe" } else { "python" }

$running = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match '^(python|pythonw|py)\.exe$' -and
        $_.CommandLine -match 'bot\.py'
    }

if ($running) {
    Write-Host "[BLOCKED] SignalBot is already running:"
    $running | Select-Object ProcessId,ParentProcessId,CommandLine | Format-Table -AutoSize
    exit 1
}

$proc = Start-Process -FilePath $python -ArgumentList "bot.py" -WorkingDirectory $root -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 5

$live = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match '^(python|pythonw|py)\.exe$' -and
        $_.CommandLine -match 'bot\.py'
    }

if ($proc.HasExited) {
    Write-Host "[ERROR] Bot exited with code: $($proc.ExitCode)"
    exit 1
}

if (($live | Measure-Object).Count -ne 1) {
    Write-Host "[ERROR] Expected exactly one SignalBot process, found $($live.Count)."
    $live | Select-Object ProcessId,ParentProcessId,CommandLine | Format-Table -AutoSize
    exit 1
}

Write-Host "[OK] SignalBot started with PID: $($live.ProcessId)"
Write-Host "[INFO] Check logs\bot.log for 'Bot ready'."
