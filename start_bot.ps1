$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$proc = Start-Process -FilePath "python" -ArgumentList "bot.py" -WorkingDirectory "C:\Users\User\Documents\botsignal" -PassThru -NoNewWindow
Start-Sleep 5
if ($proc.HasExited) { 
    Write-Host "[ERROR] Bot exited with code: $($proc.ExitCode)"
    exit 1 
}
Write-Host "[OK] Bot started with PID: $($proc.Id)"