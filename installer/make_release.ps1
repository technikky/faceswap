<#
.SYNOPSIS
    Package a single distributable ZIP of the whole project (PRS section 16).

.DESCRIPTION
    Produces dist\FaceReplacement-<date>.zip containing source, config, assets,
    test, docs, installer and (if present) the populated offline-sdk. Excludes
    build artefacts and runtime state (.venv, runtime, __pycache__, logs,
    recordings, snapshots).

.PARAMETER IncludeSdk
    Include offline-sdk/ in the package (large - the full offline bundle).
    Omit to ship a source-only package that is bundled on the target side.
#>
[CmdletBinding()]
param([switch]$IncludeSdk)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root "dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null

$stamp = (Get-Date).ToString("yyyyMMdd")
$staging = Join-Path $env:TEMP ("fr_release_" + $stamp)
if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
New-Item -ItemType Directory -Force -Path $staging | Out-Null

$exclude = @(".venv", "runtime", "dist", "__pycache__", "logs",
             "recordings", "snapshots", ".git")
if (-not $IncludeSdk) { $exclude += "offline-sdk" }

Write-Host "Staging release files..." -ForegroundColor Green
Get-ChildItem $root -Force | Where-Object { $exclude -notcontains $_.Name } | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $staging $_.Name) -Recurse -Force
}
# Strip any nested __pycache__ that slipped through.
Get-ChildItem $staging -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$zip = Join-Path $dist ("FaceReplacement-" + $stamp + ".zip")
if (Test-Path $zip) { Remove-Item $zip -Force }
Write-Host "Compressing -> $zip" -ForegroundColor Green
Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $zip -Force
Remove-Item $staging -Recurse -Force

$size = "{0:N1} MB" -f ((Get-Item $zip).Length / 1MB)
Write-Host "`nRelease package created: $zip ($size)" -ForegroundColor Cyan
if (-not $IncludeSdk) {
    Write-Host "Note: offline-sdk NOT included. Bundle it on the target side, or re-run with -IncludeSdk."
}
