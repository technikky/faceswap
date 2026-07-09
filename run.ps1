# Launch the Offline Face Replacement app.
#
# Prefers an interpreter provisioned by the offline installer:
#   1. runtime\python.exe        (bundled embeddable Python)
#   2. .venv\Scripts\python.exe  (virtual environment)
# If neither exists, falls back to creating a .venv and installing deps -
# offline from offline-sdk\python-wheels when available, otherwise from PyPI.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

$python = $null
foreach ($cand in @("runtime\python.exe", ".venv\Scripts\python.exe")) {
    $p = Join-Path $root $cand
    if (Test-Path $p) { $python = $p; break }
}

if (-not $python) {
    Write-Host "No provisioned interpreter found. Setting up a virtual environment..." -ForegroundColor Cyan
    $venv = Join-Path $root ".venv"
    python -m venv $venv
    $python = Join-Path $venv "Scripts\python.exe"
    & $python -m pip install --upgrade pip
    $wheels = Join-Path $root "offline-sdk\python-wheels"
    if (Test-Path $wheels) {
        Write-Host "Installing dependencies offline from offline-sdk..." -ForegroundColor Cyan
        & $python -m pip install --no-index --find-links $wheels -r (Join-Path $root "requirements.txt")
    } else {
        Write-Host "Installing dependencies from PyPI..." -ForegroundColor Cyan
        & $python -m pip install -r (Join-Path $root "requirements.txt")
    }
}

Write-Host "Launching application..." -ForegroundColor Green
& $python (Join-Path $root "source\run_app.py")
