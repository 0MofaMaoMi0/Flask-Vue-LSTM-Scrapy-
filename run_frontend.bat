@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%vue"

where npm >nul 2>nul
if errorlevel 1 goto :npm_missing
if exist "node_modules" goto :start

echo Installing frontend dependencies. This may take a few minutes.
call npm install
if errorlevel 1 goto :install_failed

:start
echo Starting Vue frontend at http://127.0.0.1:8999
call npm run serve
goto :end

:npm_missing
echo [ERROR] npm was not found. Install Node.js and add it to PATH.
goto :end

:install_failed
echo [ERROR] npm install failed. Check the Node.js version and network.

:end
echo.
echo Frontend process has stopped.
pause
endlocal
