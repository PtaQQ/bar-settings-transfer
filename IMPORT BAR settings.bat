@echo off
title BAR Settings Transfer - IMPORT
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bar-settings-transfer.ps1" -Mode import
echo.
pause
