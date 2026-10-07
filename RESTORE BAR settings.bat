@echo off
title BAR Settings Transfer - RESTORE
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bar-settings-transfer.ps1" -Mode restore
echo.
pause
