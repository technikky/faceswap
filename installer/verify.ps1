<#
.SYNOPSIS
    Post-install verification. Runs the headless smoke test against the
    interpreter that install.ps1 provisioned. Part of the verification workflow
    (PRS section 8).
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$python = $null
foreach ($cand in @("runtime\python.exe", ".venv\Scripts\python.exe")) {
    $p = Join-Path $root $cand
    if (Test-Path $p) { $python = $p; break }
}
if (-not $python) {
    Write-Host "No installed interpreter found. Run install.bat first." -ForegroundColor Red
    exit 1
}

Write-Host "Verifying with: $python" -ForegroundColor Cyan
& $python (Join-Path $root "test\smoke_test.py")
$rc = $LASTEXITCODE
if ($rc -eq 0) {
    Write-Host "`nVERIFICATION PASSED" -ForegroundColor Green
} else {
    Write-Host "`nVERIFICATION FAILED (exit $rc)" -ForegroundColor Red
}
exit $rc
