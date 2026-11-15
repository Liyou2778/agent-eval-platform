# Agent Evaluation Platform - one click launcher (Windows PowerShell)
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Port 8205 -NoSeed
[CmdletBinding()]
param(
    [int]$Port = 8205,
    [string]$Bind = "127.0.0.1",
    [switch]$NoSeed
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

Write-Host "== Agent Evaluation Platform (MVP) ==" -ForegroundColor Cyan
Write-Host "root : $root"

# Prefer the shared workspace venv next to this package, then PATH.
$python = $null
foreach ($candidate in @(
        (Join-Path $root "..\.venv\Scripts\python.exe"),
        (Join-Path $root ".venv\Scripts\python.exe")
    )) {
    if (Test-Path $candidate) {
        $python = (Resolve-Path $candidate).Path
        break
    }
}
if (-not $python) {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { $python = $command.Source }
}
if (-not $python) {
    throw "Python not found. Create replicas\.venv or install Python 3.12."
}
Write-Host "python : $python"
Write-Host "url    : http://${Bind}:$Port/" -ForegroundColor Green

# 1) Check dependencies, install if the venv is incomplete.
& $python -c "import fastapi, uvicorn, httpx, pydantic" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "installing dependencies from requirements.txt ..." -ForegroundColor Yellow
    & $python -m pip install -r (Join-Path $root "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

# 2) Seed demo data on first start so the dashboard is usable immediately.
$dbPath = Join-Path $root "data\eval.db"
if (-not $NoSeed -and -not (Test-Path $dbPath)) {
    Write-Host "seeding demo data (200 Chinese Q&A cases + 5 targets) ..." -ForegroundColor Yellow
    & $python (Join-Path $root "seed.py") --direct
    if ($LASTEXITCODE -ne 0) { throw "seed failed" }
}

# 3) Start the API + single page dashboard.
Write-Host "starting uvicorn (Ctrl+C to stop) ..." -ForegroundColor Green
& $python -m uvicorn app.main:app --host $Bind --port $Port
