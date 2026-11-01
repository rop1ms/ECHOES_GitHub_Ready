@echo off
title ECHOES Update
cd /d "%~dp0"
if not exist "%~dp0setup\update.en.ps1" (
    echo Extract the entire ECHOES archive before running the updater.
    pause
    exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\update.en.ps1"
