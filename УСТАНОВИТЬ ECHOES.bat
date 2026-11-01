@echo off
title ECHOES Setup
cd /d "%~dp0"
if not exist "%~dp0setup\install.ps1" (
    echo.
    echo   Setup files not found. Extract the WHOLE ECHOES_Setup.zip first,
    echo   then run this file from the extracted folder.
    echo.
    pause
    exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\install.ps1"
if errorlevel 9009 (
    echo.
    echo   PowerShell not found. Windows 10 or 11 is required.
    echo.
    pause
)
