@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "HERE=%~dp0"
set "PROJECT_PS1=%HERE%project.ps1"

if /I "%~1"=="--help" goto :help
if /I "%~1"=="--check" goto :check
if /I "%~1"=="--health" goto :health
if /I "%~1"=="--dry-run" goto :dryrun
if not "%~1"=="" goto :badarg
goto :health

:health
if not exist "%PROJECT_PS1%" (
  echo [ERROR] Project adapter not found: %PROJECT_PS1%
  exit /b 1
)
powershell.exe -NoLogo -NoProfile -File "%PROJECT_PS1%" -HealthOnly
set "RC=%ERRORLEVEL%"
echo [INFO] Backend health exit code: %RC%
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
echo Usage: check-backend.cmd [--health^|--check^|--dry-run^|--help]
exit /b 2

:help
echo Usage: check-backend.cmd [--health^|--check^|--dry-run^|--help]
echo   no args    read-only GET health through the current tunnel
echo   --health   same read-only backend health check
echo   --check    offline launcher prerequisites check; no HTTP/start
echo   --dry-run  print the start plan after health validation; no start
echo   --help     show this help
exit /b 0
