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

echo [INFO] Starting or reusing the current frontend through start-latest.ps1.
echo [INFO] No npm install, no dependency upgrade and no execution-policy bypass.
powershell.exe -NoLogo -NoProfile -File "%STARTER%"
set RC=%ERRORLEVEL%
echo [INFO] Frontend launcher exit code: %RC%
exit /b %RC%

:check
powershell.exe -NoLogo -NoProfile -File "%STARTER%" -CheckOnly
set RC=%ERRORLEVEL%
echo [INFO] Static check exit code: %RC%
exit /b %RC%

:help
echo Usage: run_frontend.bat [--check^|--help]
echo   default   start or reuse the current frontend at 5174
echo   --check   static launcher check only
exit /b 0
