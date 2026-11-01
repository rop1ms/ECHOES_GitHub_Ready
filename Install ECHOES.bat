@echo off
title ECHOES Setup
cd /d "%~dp0"
if not exist "%~dp0setup\install.en.ps1" (
    echo Extract the entire ECHOES archive before running setup.
    pause
    exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\install.en.ps1"
