# setup_vps.ps1
# One-shot setup script for SignalBot on a fresh Windows VPS.
# Run as Administrator in PowerShell.
#
# Usage:
#   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
#   .\setup_vps.ps1

$ErrorActionPreference = "Stop"
$ProjectDir = "C:\signalbot"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  SignalBot VPS Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ── 1. Check running as Administrator ────────────────────────────────────────
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: Run this script as Administrator." -ForegroundColor Red
    exit 1
}

# ── 2. Install Chocolatey (package manager) ──────────────────────────────────
Write-Host "[1/7] Installing Chocolatey..." -ForegroundColor Yellow
if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
    Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
} else {
    Write-Host "  Chocolatey already installed." -ForegroundColor Green
}

# ── 3. Install Python 3.13 ───────────────────────────────────────────────────
Write-Host "[2/7] Installing Python 3.13..." -ForegroundColor Yellow
choco install python313 -y --no-progress
refreshenv

# ── 4. Install Git ───────────────────────────────────────────────────────────
Write-Host "[3/7] Installing Git..." -ForegroundColor Yellow
choco install git -y --no-progress
refreshenv

# ── 5. Install Docker Desktop ────────────────────────────────────────────────
Write-Host "[4/7] Installing Docker Desktop..." -ForegroundColor Yellow
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    choco install docker-desktop -y --no-progress
    Write-Host "  Docker installed. You may need to restart and re-run this script." -ForegroundColor Yellow
} else {
    Write-Host "  Docker already installed." -ForegroundColor Green
}

# ── 6. Install NSSM (service manager) ───────────────────────────────────────
Write-Host "[5/7] Installing NSSM..." -ForegroundColor Yellow
choco install nssm -y --no-progress

# ── 7. Clone repo ────────────────────────────────────────────────────────────
Write-Host "[6/7] Cloning SignalBot..." -ForegroundColor Yellow
if (-not (Test-Path $ProjectDir)) {
    git clone https://github.com/jerzagit/botsignal.git $ProjectDir
} else {
    Write-Host "  Project directory already exists — pulling latest..." -ForegroundColor Green
    Set-Location $ProjectDir
    git pull
}

Set-Location $ProjectDir

# ── 8. Install Python dependencies ──────────────────────────────────────────
Write-Host "[7/7] Installing Python packages..." -ForegroundColor Yellow
pip install -r requirements.txt

# ── Create required directories ──────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path "$ProjectDir\logs" | Out-Null
New-Item -ItemType Directory -Force -Path "$ProjectDir\data" | Out-Null

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Copy .env.example to .env and fill in all values"
Write-Host "  2. Install MT5 from your broker's website"
Write-Host "  3. Run: docker-compose up -d   (starts MySQL)"
Write-Host "  4. Run: .\install_services.ps1 (registers Windows services)"
Write-Host ""
