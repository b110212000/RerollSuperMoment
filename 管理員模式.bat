@echo off
REM Keep this file pure ASCII. cmd.exe garbles non-ASCII batch text
REM because it re-parses each line in the current console codepage.
REM All Chinese messages live in _admin_setup.ps1, which is UTF-8 with BOM.

cd /d "%~dp0"
echo.
echo   Requesting administrator rights - please click "Yes" on the UAC prompt.
echo   (MLB RIVALS runs elevated, so the script must too, or Windows
echo    silently discards every click it sends.)
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-File','%~dp0_admin_setup.ps1'"

if errorlevel 1 (
    echo.
    echo   Elevation failed. If you clicked "No" on the UAC prompt,
    echo   run this file again and click "Yes".
    echo.
    pause
)
