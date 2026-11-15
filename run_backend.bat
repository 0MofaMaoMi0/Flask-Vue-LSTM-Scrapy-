@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PROJECT_ROOT=%~dp0"
set "STARTER=%PROJECT_ROOT%..\start-latest.ps1"

if /I "%~1"=="--help" goto :help
if not exist "%STARTER%" (
  echo [ERROR] Root launcher not found: %STARTER%
  exit /b 1
)

if /I "%~1"=="--check" goto :check

echo [INFO] Current backend is isolated Ubuntu loopback 5000 via 5174/3629 tunnel.
echo [INFO] This script does NOT start local Flask, MySQL or any service.
powershell.exe -NoLogo -NoProfile -File "%STARTER%" -HealthOnly
set RC=%ERRORLEVEL%
echo [INFO] Backend health check exit code: %RC%
exit /b %RC%

:check
powershell.exe -NoLogo -NoProfile -File "%STARTER%" -CheckOnly
set RC=%ERRORLEVEL%
echo [INFO] Static check exit code: %RC%
exit /b %RC%

:help
echo Usage: run_backend.bat [--check^|--help]
echo   default   read-only backend health check through the current tunnel
echo   --check   static launcher check only
exit /b 0
