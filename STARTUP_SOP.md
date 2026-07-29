# SignalBot Local Startup SOP

Use this SOP for the local Windows setup where MT5 must stay in your logged-in desktop session.

## Clean Fresh Startup

Follow this exact sequence when you want a clean fresh start.

1. Open Docker Desktop if it is not already running.
2. Open MT5 manually.
3. Login to the correct MT5 account manually.
4. Enable AutoTrading in MT5 manually.
5. Open PowerShell in the project folder:

```powershell
cd C:\Users\User\Documents\botsignal
```

6. Run clean startup:

```powershell
powershell -ExecutionPolicy Bypass -File .\start_project.ps1 -Clean
```

This will:
- stop any old `bot.py` process
- stop any old dashboard process
- remove stale bot/service lock files
- start/check MySQL Docker database
- verify DB connection from Python
- verify MT5 is already open, logged in, and AutoTrading is enabled
- start one bot process
- start dashboard DB-only on `http://127.0.0.1:5000`

Expected output should include:

```text
[OK] Database is healthy.
[OK] MT5 preflight passed.
[OK] Bot started with PID ...
[OK] Dashboard is listening on http://127.0.0.1:5000
```

The dashboard log should include:

```text
Dashboard poller disabled; MT5 will not be touched by dashboard startup.
```

## Normal Startup

Use this only when you do not want to stop existing bot/dashboard processes first:

```powershell
powershell -ExecutionPolicy Bypass -File .\start_project.ps1
```

This starts/checks:
- MySQL Docker database
- MT5 preflight, without launching/restarting MT5
- one `bot.py` process
- dashboard on `http://127.0.0.1:5000`

## Status Check

```powershell
powershell -ExecutionPolicy Bypass -File .\status_project.ps1
```

Check bot logs:

```powershell
Get-Content logs\bot.log -Tail 50
```

Expected bot log lines:

```text
MT5 startup check OK
Telegram notifier started
Bot ready
Listening on ... Telegram source(s)
```

Check dashboard logs:

```powershell
Get-Content dash_err_service.log -Tail 30
```

Expected dashboard log line:

```text
Dashboard poller disabled; MT5 will not be touched by dashboard startup.
```

## Stop

Stop bot and dashboard, keep DB and MT5 running:

```powershell
powershell -ExecutionPolicy Bypass -File .\stop_project.ps1
```

Stop bot, dashboard, and DB:

```powershell
powershell -ExecutionPolicy Bypass -File .\stop_project.ps1 -StopDatabase
```

Stop everything including MT5:

```powershell
powershell -ExecutionPolicy Bypass -File .\stop_project.ps1 -StopDatabase -StopMT5
```

## Safe Tests

Parser-only Hafiz test:

```powershell
.\.venv\Scripts\python.exe test_signal_parse.py
```

Safe mock signal test:

```powershell
.\.venv\Scripts\python.exe test_signal_inject.py
```

The mock injector does not place MT5 orders unless both `--live` and `CONFIRM_LIVE_INJECT=YES` are used.

## Important Notes

- Do not use Windows Services for the MT5-connected bot on this local machine. MT5 needs your interactive desktop session.
- Use `start_project.ps1 -Clean` for normal operation. Do not use old launchers like `run_all.bat`, `start.bat`, or `run_bot.bat` unless they are updated to the same safety flow.
- Dashboard starts DB-only by default because `DASHBOARD_POLLER_ENABLED=false`; this prevents dashboard startup from touching or restarting MT5.
- Dashboard live MT5 widgets are disabled by default because `DASHBOARD_MT5_LIVE_ENABLED=false`; this prevents the web page from probing MT5 when opened.
- The bot will not switch MT5 accounts automatically because `MT5_ALLOW_ACCOUNT_SWITCH=false`. If MT5 is on the wrong account, startup fails and asks you to switch manually.
- The bot has process and internal service duplicate guards.

Expected MT5 safety settings in `.env`:

```env
MT5_ATTACH_EXISTING_FIRST=true
MT5_ALLOW_TERMINAL_LAUNCH=false
MT5_LOCK_CONFIG=false
MT5_AUTO_TOGGLE_AUTOTRADE=false
MT5_ALLOW_ACCOUNT_SWITCH=false
DASHBOARD_POLLER_ENABLED=false
DASHBOARD_MT5_LIVE_ENABLED=false
```

Avoid running MT5-touching scripts casually:

```text
close_all.py
test_conn.py
test_mt5_conn.py
test_trade.py
test_layer.py
test_tp_split.py
```
