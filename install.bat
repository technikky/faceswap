@echo off
rem ==========================================================================
rem  Offline Face Replacement - single-click installer entry point.
rem  Double-click this file on the target machine (offline-sdk/ must be present).
rem ==========================================================================
setlocal
echo Starting offline installation...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\install.ps1" %*
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
  echo Installation finished. You can close this window.
) else (
  echo Installation exited with code %RC%. See messages above.
)
pause
