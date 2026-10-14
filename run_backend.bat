@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "PROJECT_ROOT=%~dp0"
set "PYTHON_EXE=%PROJECT_ROOT%.venv\Scripts\python.exe"

if exist "%PYTHON_EXE%" goto :start
echo [ERROR] Project virtual environment was not found:
echo %PYTHON_EXE%
echo Create .venv and install Flask requirements before starting.
goto :end

:start
cd /d "%PROJECT_ROOT%Flask"
echo Starting Flask backend at http://127.0.0.1:8080
"%PYTHON_EXE%" app.py --device cpu --batch_size 32 --network lstm

:end
echo.
echo Backend process has stopped.
pause
endlocal
