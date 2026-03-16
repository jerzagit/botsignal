# SignalBot — VPS Deployment Guide

## Important: Windows VPS Required

The MetaTrader5 Python library connects to MT5 via **Windows IPC** (named pipes).
This only works on Windows. A Linux VPS will not work.

| Component | Runs in Docker? | Why |
|-----------|----------------|-----|
| MySQL | ✅ Yes | Pure database, platform-independent |
| Bot (`bot.py`) | ❌ No | Needs Windows IPC to MT5 terminal |
| Dashboard (`dashboard/app.py`) | ❌ No | Poller needs Windows IPC to MT5 |
| MT5 Terminal | ❌ No | Windows GUI application |

Docker is used only for MySQL. Bot and Dashboard run as **Windows Services** via NSSM.

---

## Architecture

```
┌─────────────────── Windows VPS ────────────────────────────────┐
│                                                                 │
│  ┌─────────────────┐    ┌──────────────────────────────────┐   │
│  │  MT5 Terminal   │◄───│  SignalBot (Windows Service)     │   │
│  │  (runs as Admin)│    │  python bot.py                   │   │
│  │  VTMarkets-Live │    │  Auto-restarts on crash          │   │
│  └─────────────────┘    └──────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────────────────────────┐                          │
│  │  Dashboard (Windows Service)     │◄──── Browser (port 5000) │
│  │  python dashboard/app.py         │                          │
│  │  Auto-restarts on crash          │                          │
│  └──────────────────────────────────┘                          │
│                                                                 │
│  ┌──────────────────────────────────┐                          │
│  │  MySQL (Docker container)        │                          │
│  │  mysql-docker  port 3307         │                          │
│  └──────────────────────────────────┘                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Recommended VPS Providers

| Provider | Plan | Price | Notes |
|----------|------|-------|-------|
| **Contabo** | VPS S Windows | ~€7/mo | Cheapest, good for this use case |
| **Vultr** | Cloud Compute 4GB | ~$24/mo | Fast setup, hourly billing |
| **AWS EC2** | t3.small Windows | ~$15/mo | Reliable, more complex setup |
| **Azure** | B2s Windows | ~$18/mo | Good if you use MS ecosystem |

### Minimum Specs
- **OS:** Windows Server 2019 or 2022
- **RAM:** 4 GB (MT5 ~500MB + Bot ~200MB + MySQL ~300MB)
- **CPU:** 2 vCPUs
- **Disk:** 50 GB SSD
- **Network:** Any — bot uses very little bandwidth

---

## Step-by-Step Deployment

### Step 1 — Get a Windows VPS

1. Choose a provider above
2. Select **Windows Server 2019/2022**
3. Note down the **IP address** and **RDP credentials**

---

### Step 2 — Connect via RDP

**Windows:** Press `Win + R` → type `mstsc` → enter VPS IP

**Mac:** Download **Microsoft Remote Desktop** from App Store

Login with the credentials from your VPS provider.

---

### Step 3 — Enable PowerShell script execution

Open **PowerShell as Administrator**:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

### Step 4 — Run the setup script

Download and run the one-shot setup script (installs Python, Git, Docker, NSSM):

```powershell
# Option A — if you have the project files already
cd C:\signalbot
.\setup_vps.ps1

# Option B — fresh machine (download and run)
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/jerzagit/botsignal/master/setup_vps.ps1" -OutFile "C:\setup_vps.ps1"
& "C:\setup_vps.ps1"
```

This installs:
- Python 3.13
- Git
- Docker Desktop
- NSSM (Windows service manager)
- Clones the repo to `C:\signalbot`
- Installs all Python packages

> **After Docker Desktop installs, you may need to restart Windows once, then re-run `setup_vps.ps1`.**

---

### Step 5 — Install MT5

1. Download MT5 from **your broker's website** (VT Markets → Client Portal → MetaTrader 5)
2. Install it on the VPS
3. Open MT5 → log in with your trading account
4. Go to **Tools → Options → Expert Advisors** → tick **"Allow algorithmic trading"**
5. Right-click the MT5 shortcut → **Run as Administrator** → login again

> MT5 must be running as Administrator **before** the bot starts.
> On a VPS, you need to keep it open (it cannot be minimised to tray while disconnected from RDP — see Step 9 for the fix).

---

### Step 6 — Configure .env

```powershell
cd C:\signalbot
copy .env.example .env
notepad .env
```

Fill in all values. Key ones for VPS:

```env
MT5_PATH=C:\Program Files\VT Markets (Pty) MT5 Terminal\terminal64.exe
MT5_LOGIN=26656038
MT5_PASSWORD=your_password
MT5_SERVER=VTMarkets-Live 5
MT5_SYMBOL_SUFFIX=-STD

DB_HOST=localhost
DB_PORT=3307
DB_PASSWORD=rootpass
```

---

### Step 7 — Start MySQL

```powershell
cd C:\signalbot
docker-compose up -d
```

Verify it's running:
```powershell
docker ps
```

You should see `mysql-docker` with status `healthy`.

---

### Step 8 — Install and start Windows services

```powershell
cd C:\signalbot
.\install_services.ps1
```

This registers two Windows services:
- **SignalBot** — runs `bot.py`, auto-starts on boot, auto-restarts on crash
- **SignalBotDashboard** — runs `dashboard/app.py`, same behaviour

Verify services are running:
```powershell
Get-Service SignalBot, SignalBotDashboard
```

Expected output:
```
Status   Name                  DisplayName
------   ----                  -----------
Running  SignalBot             SignalBot
Running  SignalBotDashboard    SignalBotDashboard
```

---

### Step 9 — Keep MT5 running when RDP disconnects

By default, Windows kills GUI apps when you close the RDP session. Fix this:

**Option A — Task Scheduler (recommended)**

1. Open **Task Scheduler** → Create Task
2. Name: `MT5 AutoStart`
3. Trigger: **At log on** (for your user)
4. Action: Start program → `C:\Program Files\VT Markets (Pty) MT5 Terminal\terminal64.exe`
5. Settings: tick **"Run with highest privileges"**

**Option B — tscon trick (keeps session alive)**

Before closing RDP, open Command Prompt as Administrator and run:
```cmd
tscon %SESSIONNAME% /dest:console
```
This detaches your RDP session without logging out — MT5 keeps running.

---

### Step 10 — Access the dashboard remotely

The dashboard runs on port 5000. To access it from your browser:

**Option A — Direct access (simple, less secure)**

Open VPS firewall port 5000:
- AWS: Security Group → add inbound rule TCP 5000
- Azure: Network Security Group → add inbound rule TCP 5000
- Contabo/Vultr: Firewall → add rule TCP 5000

Then visit: `http://YOUR-VPS-IP:5000`

**Option B — SSH tunnel (secure, no firewall changes)**

From your local machine:
```bash
ssh -L 5000:localhost:5000 Administrator@YOUR-VPS-IP
```
Then visit: `http://localhost:5000`

---

## Service Management

```powershell
# Check status
Get-Service SignalBot, SignalBotDashboard

# Start
Start-Service SignalBot
Start-Service SignalBotDashboard

# Stop
Stop-Service SignalBot
Stop-Service SignalBotDashboard

# Restart
Restart-Service SignalBot

# View logs
Get-Content C:\signalbot\logs\bot.log -Tail 50 -Wait

# MySQL
docker-compose up -d       # start
docker-compose down        # stop
docker-compose logs -f     # tail logs
```

---

## Updating the bot

```powershell
# Stop services
Stop-Service SignalBot, SignalBotDashboard

# Pull latest code
cd C:\signalbot
git pull

# Install any new packages
pip install -r requirements.txt

# Restart services
Start-Service SignalBot, SignalBotDashboard
```

---

## Auto-start everything on reboot

| Component | Auto-start method |
|-----------|------------------|
| MySQL | Docker restart policy: `always` (set in docker-compose.yml) |
| SignalBot | Windows Service: `SERVICE_AUTO_START` (set by NSSM) |
| Dashboard | Windows Service: `SERVICE_AUTO_START` (set by NSSM) |
| MT5 | Task Scheduler trigger: At log on |
| Docker Desktop | Docker Desktop Settings → Start on boot |

After a VPS reboot, everything comes back up automatically — no manual intervention needed.

---

## Files created by this guide

| File | Purpose |
|------|---------|
| `docker-compose.yml` | MySQL container config |
| `db/init.sql` | MySQL schema (auto-applied on first run) |
| `setup_vps.ps1` | One-shot VPS setup script |
| `install_services.ps1` | Registers bot + dashboard as Windows services |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Services show "Stopped" | Check logs: `Get-Content C:\signalbot\logs\bot.log -Tail 50` |
| MT5 IPC timeout | Make sure MT5 is open as Administrator and logged in |
| Docker not found | Restart Windows after Docker Desktop install |
| Port 5000 not accessible | Open firewall rule or use SSH tunnel |
| MySQL won't start | `docker-compose logs mysql` — check for port conflict on 3307 |
| Bot crashes on start | Check `.env` — all values must be filled in |
| MT5 closes when RDP disconnects | Use Task Scheduler or tscon trick (Step 9) |
| `cryptography` error | `pip install cryptography` |
