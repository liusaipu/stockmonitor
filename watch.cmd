@echo off
chcp 65001 >nul 2>&1
powershell -ExecutionPolicy Bypass -File "%~dp0watch_quotes.ps1" %*
if errorlevel 1 pause
