@echo off
cd /d "%~dp0"
echo Starting SignalBot on LIVE account...
echo.
start "SignalBot" cmd /k "python bot.py"
echo Bot started in new window. Check logs\bot.log for status.
