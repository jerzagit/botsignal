# SignalBot VPS Installation Guide

Complete guide for deploying SignalBot + Docker + OpenCode on a Windows VPS.

**VPS Specs:** 2 vCPU, 4 GB RAM, 50 GB NVMe, Windows Server 2012/2019/2022

---

## 1 — RDP into your VPS

Open Remote Desktop (`Win+R` → `mstsc`), enter your VPS IP and credentials.

---

## 2 — Install dependencies (one-shot)

Open **PowerShell as Administrator** and run:

```powershell
# Enable script execution
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser

# Install Chocolatey (package manager)
Set-ExecutionPolicy Bypass -Scope Process -Force
[System.Net.ServicePointManager]::SecurityProtocol = 3072
Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))

# Install Python 3.13, Git, Docker Desktop, NSSM
choco install python313 git docker-desktop nssm -y --no-progress
```

**Restart the VPS** after Docker Desktop finishes installing.

After reboot, re-open PowerShell as Admin and run `refreshenv` or log out/in for PATH changes to take effect.

---

## 3 — Clone the bot & install Python deps

```powershell
cd C:\
git clone https://github.com/jerzagit/botsignal.git C:\signalbot
cd C:\signalbot
pip install -r requirements.txt
```

---

## 4 — Install MT5

1. Download MT5 from **your broker's website** (not MetaQuotes)
2. Install it on the VPS
3. Open MT5 → log in with your trading account
4. Go to **Tools → Options → Expert Advisors** → tick **"Allow algorithmic trading"**
5. Click the **Algo Trading** button in the toolbar — must be **green**
6. Right-click the MT5 shortcut → **Run as Administrator** → login again

> MT5 must be running as Administrator **before** the bot starts.
> On a VPS, keep it open (see Step 9 for keeping it alive after RDP disconnect).

---

## 5 — Configure `.env`

```powershell
cd C:\signalbot
copy .env.example .env
notepad .env
```

Fill in all values:
- Telegram API ID, API hash, bot token
- Your chat ID and signal group ID
- MT5 credentials (demo and/or live)
- MySQL settings (defaults work if using Docker below)

---

## 6 — Start MySQL via Docker

```powershell
cd C:\signalbot
docker-compose up -d
```

Verify it's running:

```powershell
docker ps
```

Expected output — `botsignal-mysql` with status `healthy`.

---

## 7 — Install OpenCode CLI

Pick **one** method:

### Option A — via Chocolatey (simplest, already installed)
```powershell
choco install opencode -y
```

### Option B — via npm (requires Node.js)
```powershell
npm install -g opencode-ai@latest
```

### Option C — via Scoop
```powershell
scoop install opencode
```

Verify installation:

```powershell
opencode --version
```

Then configure your API key (e.g., Anthropic, OpenAI, etc.):

```powershell
opencode config set OPENCODE_API_KEY=your-api-key
opencode config set OPENCODE_PROVIDER=anthropic
```

---

## 8 — Register Windows services (auto-start on boot)

```powershell
cd C:\signalbot
.\install_services.ps1
```

This registers two Windows services:

| Service | Runs | Auto-start | Auto-restart |
|---------|------|-----------|-------------|
| **SignalBot** | `python bot.py` | On boot | On crash |
| **SignalBotDashboard** | `python dashboard/app.py` | On boot | On crash |

Verify services are running:

```powershell
Get-Service SignalBot, SignalBotDashboard
```

Expected:

```
Status   Name                  DisplayName
------   ----                  -----------
Running  SignalBot             SignalBot
Running  SignalBotDashboard    SignalBotDashboard
```

### Useful service commands

```powershell
Start-Service SignalBot           # start
Stop-Service SignalBot            # stop
Restart-Service SignalBot         # restart
Get-Content C:\signalbot\logs\bot.log -Tail 50 -Wait   # tail logs
```

---

## 9 — Keep MT5 running when RDP disconnects

By default, Windows kills GUI apps when you close RDP. Fix this:

### Option A — Task Scheduler (recommended)

1. Open **Task Scheduler** → Create Task
2. Name: `MT5 AutoStart`
3. Trigger: **At log on** (for your user)
4. Action: Start program → path to `terminal64.exe`
5. Settings: tick **"Run with highest privileges"**

### Option B — tscon trick (run before closing RDP)

```cmd
tscon %SESSIONNAME% /dest:console
```

This detaches your RDP session without logging out — MT5 keeps running.

---

## 10 — Access the dashboard remotely

The dashboard runs on `http://localhost:5000`.

### Option A — Direct access

Open VPS firewall port 5000, then visit `http://YOUR-VPS-IP:5000`.

### Option B — SSH tunnel (secure, no firewall changes)

```bash
ssh -L 5000:localhost:5000 Administrator@YOUR-VPS-IP
```

Then visit `http://localhost:5000`.

---

## Updating the bot

```powershell
Stop-Service SignalBot, SignalBotDashboard
cd C:\signalbot
git pull
pip install -r requirements.txt
Start-Service SignalBot, SignalBotDashboard
```

---

## Auto-start everything on reboot

| Component | Auto-start method |
|-----------|------------------|
| MySQL | Docker restart policy: `always` (set in docker-compose.yml) |
| SignalBot | Windows Service: auto-start (set by NSSM) |
| Dashboard | Windows Service: auto-start (set by NSSM) |
| MT5 | Task Scheduler trigger: At log on |
| Docker Desktop | Docker Desktop Settings → Start on boot |

After a VPS reboot, everything comes back automatically — no manual intervention needed.

---

## Memory usage breakdown

| Component | RAM |
|-----------|-----|
| Windows OS | ~1.5 GB |
| MT5 Terminal | ~500 MB |
| SignalBot | ~200 MB |
| Dashboard | ~100 MB |
| MySQL (Docker) | ~300 MB |
| OpenCode (idle) | ~50 MB |
| **Total** | **~2.65 GB** |

4 GB RAM is sufficient with ~1.3 GB headroom.
