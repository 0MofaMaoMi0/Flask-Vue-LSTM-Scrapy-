@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PROJECT_PYTHON=%~dp0.local-cache\auth-venv\Scripts\python.exe"
set "PROJECT_STOPPER=%~dp0scripts\stop_project.py"
if not exist "%PROJECT_PYTHON%" goto :missing
if not exist "%PROJECT_STOPPER%" goto :missing
"%PROJECT_PYTHON%" -B "%PROJECT_STOPPER%" %*
set "RC=%ERRORLEVEL%"
if not "%~1"=="" exit /b %RC%
if "%RC%"=="0" goto :ok
echo.
echo [ERROR] Stop failed. The message above explains the cause.
pause
exit /b %RC%
:ok
echo.
echo [INFO] Project services stopped.
timeout /t 2 >nul
exit /b 0
:missing
echo [ERROR] Existing Python environment or project stopper is missing.
echo No dependencies were installed. Restore the existing project environment first.
if "%~1"=="" pause
exit /b 2
