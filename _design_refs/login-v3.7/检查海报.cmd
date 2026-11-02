@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0download_posters.ps1" -CheckOnly
if %errorlevel%==0 (echo All 32 posters OK.) else (echo Some posters are missing or invalid.)
pause
