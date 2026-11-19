@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PROJECT_PYTHON=%~dp0.local-cache\auth-venv\Scripts\python.exe"
set "PROJECT_LAUNCHER=%~dp0scripts\launch_project.py"
if not exist "%PROJECT_PYTHON%" goto :missing
if not exist "%PROJECT_LAUNCHER%" goto :missing
"%PROJECT_PYTHON%" -B "%PROJECT_LAUNCHER%" %*
set "RC=%ERRORLEVEL%"
if not "%~1"=="" exit /b %RC%
if "%RC%"=="0" exit /b 0
echo.
echo [ERROR] Startup failed. The message above explains the cause.
pause
exit /b %RC%
:missing
echo [ERROR] Existing Python environment or project launcher is missing.
echo No dependencies were installed. Restore the existing project environment first.
if "%~1"=="" pause
exit /b 2
