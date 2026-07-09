<#
.SYNOPSIS
    Populate offline-sdk/ with everything needed to install the application on
    an air-gapped machine. Run this ONCE on an internet-connected Windows x64
    machine that has Python 3.10, 3.11, or 3.12 (64-bit) available.

.DESCRIPTION
    Downloads, into ProjectRoot/offline-sdk/:
      * python-wheels/   all pip wheels for requirements.txt (+ pip/setuptools/wheel)
      * python/          the Windows embeddable Python runtime + get-pip.py
      * vcredist/        the Visual C++ 2015-2022 x64 runtime installer
    and writes manifest.json describing the bundle.

    IMPORTANT: the embeddable Python runtime is selected to MATCH the minor
    version of the Python you run this with, so the downloaded wheels (which are
    version-specific, e.g. cp311) always match the runtime the installer
    provisions. FFmpeg is provided by the imageio-ffmpeg wheel.

.NOTES
    Run the corresponding install.bat on the OFFLINE target.
#>
[CmdletBinding()]
param(
    [string]$EmbedUrl    = "",   # override the auto-selected embeddable Python URL
    [string]$GetPipUrl   = "https://bootstrap.pypa.io/get-pip.py",
    [string]$VcRedistUrl = "https://aka.ms/vs/17/release/vc_redist.x64.exe",
    [string]$FaceLandmarkerUrl = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
)

$ErrorActionPreference = "Stop"
$root    = Split-Path -Parent $PSScriptRoot
$sdk     = Join-Path $root "offline-sdk"
$wheels  = Join-Path $sdk  "python-wheels"
$pyDir   = Join-Path $sdk  "python"
$vcDir   = Join-Path $sdk  "vcredist"
$req     = Join-Path $root "requirements.txt"

foreach ($d in @($sdk, $wheels, $pyDir, $vcDir)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

Write-Host "== Offline SDK bundler ==" -ForegroundColor Cyan
Write-Host "Project root : $root"
Write-Host "Target SDK   : $sdk`n"

# 0) Validate the bundling Python and pick a matching embeddable runtime -----
# Resolve a supported interpreter via the py launcher first (so PATH order and a
# newer default Python like 3.14 don't interfere), then fall back to `python`.
$pyExe = $null
foreach ($v in @("3.11", "3.12", "3.10")) {
    try {
        $cand = (& py "-$v" -c "import sys;print(sys.executable)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $cand) { $pyExe = $cand.Trim(); break }
    } catch {}
}
if (-not $pyExe -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $pyExe = (python -c "import sys;print(sys.executable)").Trim()
}
if (-not $pyExe) {
    throw "No Python 3.10/3.11/3.12 (64-bit) found. Install Python 3.11 (64-bit) to bundle."
}
Write-Host "Interpreter  : $pyExe"
$pyv  = (& $pyExe -c "import sys;print('%d.%d'%sys.version_info[:2])").Trim()
$bits = (& $pyExe -c "import struct;print(struct.calcsize('P')*8)").Trim()

if ($bits -ne "64") {
    throw "Bundling Python is $bits-bit. A 64-bit Python is required (the app targets Windows x64)."
}

# Embeddable runtime patch releases per supported minor version.
$embedMap = @{
    "3.10" = "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip"
    "3.11" = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
    "3.12" = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip"
}
if (-not $embedMap.ContainsKey($pyv)) {
    throw ("Bundling Python is $pyv, which is not supported. Use Python 3.10, 3.11 or 3.12 " +
           "(64-bit) - MediaPipe and the numpy<2 pin have no wheels outside that range. " +
           "3.11 is recommended.")
}
if (-not $EmbedUrl) { $EmbedUrl = $embedMap[$pyv] }
$cpTag = "cp" + ($pyv -replace '\.', '')   # 3.11 -> cp311
Write-Host "Bundling with Python $pyv (64-bit) -> wheel tag $cpTag" -ForegroundColor Yellow
Write-Host "Embeddable runtime: $EmbedUrl`n"

# 1) Python wheels for all requirements ------------------------------------
Write-Host "[1/4] Downloading Python wheels..." -ForegroundColor Green
& $pyExe -m pip download -r $req -d $wheels
# Ensure pip/setuptools/wheel are present for offline bootstrap.
& $pyExe -m pip download pip setuptools wheel -d $wheels
Write-Host ("      {0} wheel files" -f (Get-ChildItem $wheels -File).Count)

# Sanity check: a numpy wheel matching this interpreter must be present.
$numpyWheel = Get-ChildItem $wheels -Filter ("numpy-*{0}-*win_amd64.whl" -f $cpTag) -ErrorAction SilentlyContinue
if (-not $numpyWheel) {
    throw ("No numpy wheel tagged $cpTag was downloaded. The wheels do not match the " +
           "embeddable runtime. Re-run this script with Python $pyv, or check your network.")
}
Write-Host ("      verified: {0}" -f $numpyWheel.Name)

# 2) Embeddable Python runtime + get-pip.py --------------------------------
Write-Host "[2/4] Downloading embeddable Python ($pyv)..." -ForegroundColor Green
$embedZip = Join-Path $pyDir "python-embed.zip"
Invoke-WebRequest -Uri $EmbedUrl -OutFile $embedZip -UseBasicParsing
$embedDir = Join-Path $pyDir "embed"
if (Test-Path $embedDir) { Remove-Item $embedDir -Recurse -Force }
Expand-Archive -Path $embedZip -DestinationPath $embedDir -Force
Remove-Item $embedZip -Force
Invoke-WebRequest -Uri $GetPipUrl -OutFile (Join-Path $pyDir "get-pip.py") -UseBasicParsing
Write-Host "      embeddable runtime -> $embedDir"

# 3) Visual C++ runtime -----------------------------------------------------
Write-Host "[3/5] Downloading Visual C++ 2015-2022 x64 runtime..." -ForegroundColor Green
Invoke-WebRequest -Uri $VcRedistUrl -OutFile (Join-Path $vcDir "vc_redist.x64.exe") -UseBasicParsing

# 4) MediaPipe model (Tasks API FaceLandmarker) ----------------------------
Write-Host "[4/5] Downloading MediaPipe face_landmarker model..." -ForegroundColor Green
$mpDir = Join-Path $root "assets\models\mediapipe"
New-Item -ItemType Directory -Force -Path $mpDir | Out-Null
$mpModel = Join-Path $mpDir "face_landmarker.task"
Invoke-WebRequest -Uri $FaceLandmarkerUrl -OutFile $mpModel -UseBasicParsing
Write-Host ("      face_landmarker.task ({0:N2} MB)" -f ((Get-Item $mpModel).Length/1MB))

# 5) Manifest ---------------------------------------------------------------
Write-Host "[5/5] Writing manifest..." -ForegroundColor Green
$manifest = [ordered]@{
    bundled_on      = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    python_minor    = $pyv
    wheel_tag       = $cpTag
    embed_url       = $EmbedUrl
    wheel_count     = (Get-ChildItem $wheels -File).Count
    wheels          = (Get-ChildItem $wheels -File | Select-Object -ExpandProperty Name)
    has_embeddable  = (Test-Path (Join-Path $embedDir "python.exe"))
    has_vcredist    = (Test-Path (Join-Path $vcDir "vc_redist.x64.exe"))
    has_mp_model    = (Test-Path $mpModel)
}
$manifest | ConvertTo-Json -Depth 4 | Out-File (Join-Path $sdk "manifest.json") -Encoding utf8

Write-Host "`nDone. offline-sdk/ (Python $pyv / $cpTag) is ready to copy to the target." -ForegroundColor Cyan
Write-Host "Next: copy the whole ProjectRoot to the target and run install.bat"
