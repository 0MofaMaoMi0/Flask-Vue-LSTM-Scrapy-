@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "HERE=%~dp0"
set "PROJECT_PS1=%HERE%project.ps1"

if /I "%~1"=="--help" goto :help
if /I "%~1"=="--check" goto :check
if /I "%~1"=="--dry-run" goto :dryrun
if not "%~1"=="" goto :badarg
if not exist "%PROJECT_PS1%" (
  echo [ERROR] Project adapter not found: %PROJECT_PS1%
  exit /b 1
)

echo [INFO] Starting or reusing the current frontend at 5174.
echo [INFO] No package installation, no dependency upgrade and no execution-policy bypass.
powershell.exe -NoLogo -NoProfile -File "%PROJECT_PS1%"
set "RC=%ERRORLEVEL%"
echo [INFO] Frontend launcher exit code: %RC%
exit /b %RC%

:check
if not exist "%PROJECT_PS1%" (
  echo [ERROR] Project adapter not found: %PROJECT_PS1%
  exit /b 1
)
powershell.exe -NoLogo -NoProfile -File "%PROJECT_PS1%" -CheckOnly
set "RC=%ERRORLEVEL%"
echo [INFO] Static check exit code: %RC%
exit /b %RC%

:dryrun
if not exist "%PROJECT_PS1%" (
  echo [ERROR] Project adapter not found: %PROJECT_PS1%
  exit /b 1
)
powershell.exe -NoLogo -NoProfile -File "%PROJECT_PS1%" -DryRun
set "RC=%ERRORLEVEL%"
echo [INFO] Dry-run exit code: %RC%
exit /b %RC%

:badarg
echo [ERROR] Unknown argument: %~1
echo Usage: start-frontend.cmd [--check^|--dry-run^|--help]
exit /b 2

:help
echo Usage: start-frontend.cmd [--check^|--dry-run^|--help]
echo   no args    start or reuse the current frontend at 5174
echo   --check    offline launcher prerequisites check; no HTTP/start
echo   --dry-run  validate tunnel/backend and print the start plan; no start
echo   --help     show this help
exit /b 0
