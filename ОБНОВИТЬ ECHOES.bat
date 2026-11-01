@echo off
title ECHOES Update
cd /d "%~dp0"
if not exist "%~dp0setup\update.ps1" (
    echo.
    echo   Update files not found. Extract the WHOLE archive first,
    echo   then run this file from the extracted folder.
    echo.
    pause
    exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\update.ps1"
