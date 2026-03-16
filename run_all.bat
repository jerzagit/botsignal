@echo off
echo Starting SignalBot + Dashboard...
start "SignalBot" cmd /k "cd /d %~dp0 && python bot.py"
start "Dashboard" cmd /k "cd /d %~dp0 && python dashboard/app.py"
echo Both started. Open http://localhost:5000 in your browser.
