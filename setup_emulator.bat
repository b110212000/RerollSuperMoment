@echo off
REM ---------------------------------------------------------------
REM Launcher for setup_emulator.ps1
REM
REM This file MUST stay pure ASCII with no BOM. cmd.exe re-parses the
REM file line by line in the console codepage, so a BOM or any non-ASCII
REM byte here corrupts the parse. All Chinese text lives in the .ps1,
REM which is UTF-8 with BOM as PowerShell 5.1 requires.
REM ---------------------------------------------------------------

setlocal
set PS1=%~dp0setup_emulator.ps1

if not exist "%PS1%" (
    echo [X] setup_emulator.ps1 not found next to this file.
    pause
    exit /b 1
)

REM -ExecutionPolicy Bypass so an unsigned local script can run without
REM permanently loosening the machine policy.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set RC=%ERRORLEVEL%

echo.
if %RC% NEQ 0 echo [X] exit code %RC%
pause
exit /b %RC%
