@echo off
title BAR Settings Transfer - EXPORT
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bar-settings-transfer.ps1" -Mode export
echo.
pause
