<#
.SYNOPSIS
    Offline installer. Sets up the application on an air-gapped Windows machine
    using only the contents of offline-sdk/. No internet access is used.

.DESCRIPTION
    1. Installs the Visual C++ runtime (if bundled and not already present).
    2. Provisions a Python interpreter:
         - preferred: the bundled embeddable Python -> ProjectRoot/runtime/
         - fallback : an existing Python 3.10/3.11 on the machine (creates .venv)
    3. Installs all wheels from offline-sdk/python-wheels (no index).
    4. Runs verification (test/smoke_test.py).
    5. Writes "Launch Face Replacement.bat" at the project root.

.NOTES
    Normally invoked via install.bat (double-click). Requires offline-sdk/ to
    have been populated by installer\bundle_offline_sdk.ps1 first.
#>
[CmdletBinding()]
param(
    [switch]$SystemPython,   # force using an existing Python instead of embeddable
    [switch]$SkipVerify
)

$ErrorActionPreference = "Stop"
$root    = Split-Path -Parent $PSScriptRoot
$sdk     = Join-Path $root "offline-sdk"
$wheels  = Join-Path $sdk  "python-wheels"
$req     = Join-Path $root "requirements.txt"

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

Write-Host "== Offline Face Replacement - Installer ==" -ForegroundColor Cyan
Write-Host "Project root: $root`n"

if (-not (Test-Path $wheels)) {
    Fail "offline-sdk\python-wheels not found. Run installer\bundle_offline_sdk.ps1 on a connected machine first."
}

# 1) Visual C++ runtime -----------------------------------------------------
$vcRedist = Join-Path $sdk "vcredist\vc_redist.x64.exe"
if (Test-Path $vcRedist) {
    Write-Host "[1/5] Installing Visual C++ runtime (quiet)..." -ForegroundColor Green
    $p = Start-Process -FilePath $vcRedist -ArgumentList "/install", "/quiet", "/norestart" -PassThru -Wait
    # 0 = installed, 1638/3010 = already present / reboot needed - all OK.
    if ($p.ExitCode -notin @(0, 1638, 3010)) {
        Write-Warning "vc_redist returned exit code $($p.ExitCode); continuing."
    }
} else {
    Write-Host "[1/5] No bundled VC++ runtime; skipping." -ForegroundColor DarkGray
}

# 2) Provision the interpreter ---------------------------------------------
Write-Host "[2/5] Provisioning Python interpreter..." -ForegroundColor Green
$python = $null
$embedSrc = Join-Path $sdk "python\embed"
$runtime  = Join-Path $root "runtime"

if (-not $SystemPython -and (Test-Path (Join-Path $embedSrc "python.exe"))) {
    # --- Embeddable Python: copy into runtime/, enable site-packages, bootstrap pip
    Write-Host "      Using bundled embeddable Python."
    if (Test-Path $runtime) { Remove-Item $runtime -Recurse -Force }
    Copy-Item $embedSrc $runtime -Recurse -Force

    # Enable site-packages so pip-installed packages import.
    $pth = Get-ChildItem $runtime -Filter "python*._pth" | Select-Object -First 1
    if ($pth) {
        $lines = Get-Content $pth.FullName
        $lines = $lines | ForEach-Object { if ($_ -match '^\s*#\s*import\s+site') { "import site" } else { $_ } }
        if ($lines -notcontains "import site")           { $lines += "import site" }
        if ($lines -notcontains "Lib\site-packages")     { $lines += "Lib\site-packages" }
        Set-Content -Path $pth.FullName -Value $lines -Encoding ascii
    }
    $python = Join-Path $runtime "python.exe"

    $getpip = Join-Path $sdk "python\get-pip.py"
    if (-not (Test-Path $getpip)) { Fail "offline-sdk\python\get-pip.py missing." }
    Write-Host "      Bootstrapping pip (offline)..."
    & $python $getpip --no-index --find-links $wheels --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { Fail "pip bootstrap failed." }
} else {
    # --- Fallback: an existing Python 3.11 / 3.10 -> venv
    Write-Host "      Looking for an installed Python 3.11/3.10..."
    $base = $null
    foreach ($v in @("3.11", "3.10")) {
        try { & py "-$v" -c "import sys" 2>$null; if ($?) { $base = "py -$v"; break } } catch {}
    }
    if (-not $base) { Fail "No embeddable bundle and no Python 3.10/3.11 found. Re-bundle with the embeddable runtime." }
    $venv = Join-Path $root ".venv"
    if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
        Write-Host "      Creating virtual environment with $base..."
        Invoke-Expression "$base -m venv `"$venv`""
    }
    $python = Join-Path $venv "Scripts\python.exe"
}

# 3) Install dependencies (offline) ----------------------------------------
Write-Host "[3/5] Installing dependencies from offline-sdk (no index)..." -ForegroundColor Green

# Guard against a wheel/interpreter version mismatch with a clear message.
$cpTag = (& $python -c "import sys;print('cp%d%d'%sys.version_info[:2])").Trim()
$numpyWheel = Get-ChildItem $wheels -Filter ("numpy-*{0}-*win_amd64.whl" -f $cpTag) -ErrorAction SilentlyContinue
if (-not $numpyWheel) {
    $have = (Get-ChildItem $wheels -Filter "numpy-*.whl" | Select-Object -ExpandProperty Name) -join ", "
    Fail ("The interpreter is $cpTag but offline-sdk has no matching numpy wheel. " +
          "Found: [$have]. The bundle was built for a different Python version. " +
          "Re-run installer\bundle_offline_sdk.ps1 with Python $($cpTag -replace 'cp(\d)(\d+)','$1.$2') " +
          "(or bundle with your Python, which now auto-matches the runtime), then recopy offline-sdk.")
}

& $python -m pip install --no-index --find-links $wheels -r $req --no-warn-script-location
if ($LASTEXITCODE -ne 0) { Fail "Offline dependency install failed. Check offline-sdk\python-wheels completeness." }

# 4) Verify -----------------------------------------------------------------
if (-not $SkipVerify) {
    Write-Host "[4/5] Verifying installation..." -ForegroundColor Green
    & $python (Join-Path $root "test\smoke_test.py")
    if ($LASTEXITCODE -ne 0) { Write-Warning "Verification reported problems (see output above)." }
} else {
    Write-Host "[4/5] Verification skipped." -ForegroundColor DarkGray
}

# 5) Launcher ---------------------------------------------------------------
Write-Host "[5/5] Writing launcher..." -ForegroundColor Green
$relPython = $python.Substring($root.Length).TrimStart('\')
$launcher = Join-Path $root "Launch Face Replacement.bat"
@"
@echo off
rem Auto-generated by installer\install.ps1
cd /d "%~dp0"
"%~dp0$relPython" "%~dp0source\run_app.py"
"@ | Out-File -FilePath $launcher -Encoding ascii

Write-Host "`nInstallation complete." -ForegroundColor Cyan
Write-Host "Launch with:  `"$launcher`""
Write-Host "Interpreter:  $python"
