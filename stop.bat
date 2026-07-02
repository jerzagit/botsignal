@echo off
cd /d "%~dp0"
echo Stopping SignalBot...
echo.
if exist data\bot.pid (
    set /p PID=<data\bot.pid
    echo Found bot PID: %PID%
    taskkill /F /PID %PID% 2>nul && echo Bot process killed || echo Bot process not found
    del data\bot.pid 2>nul
    del data\startup.timestamp 2>nul
) else (
    echo No PID file found. Searching for running bot...
    for /f "tokens=2 delims=," %%a in ('wmic process where "name='python.exe' and commandline like '%%bot.py%%'" get ProcessId /format:csv 2^>nul') do (
        taskkill /F /PID %%a 2>nul && echo Killed bot PID: %%a
    )
)
echo.
echo Closing MT5 terminal...
taskkill /F /IM terminal64.exe 2>nul && echo MT5 terminal closed || echo No MT5 terminal running
echo.
echo SignalBot stopped.
