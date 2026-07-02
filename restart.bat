@echo off
cd /d "%~dp0"
echo Restarting SignalBot...
echo.
call stop.bat
echo.
echo Waiting 5 seconds before starting...
ping -n 5 127.0.0.1 >nul
call start.bat
echo Done.
