@echo off
title BAR Settings Transfer
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bar-settings-transfer.ps1" -Mode menu
echo.
pause
