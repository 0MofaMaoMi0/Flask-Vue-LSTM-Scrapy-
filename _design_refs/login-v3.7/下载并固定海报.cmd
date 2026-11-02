@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0download_posters.ps1"
echo.
echo Poster installer finished. Check the result above.
pause
