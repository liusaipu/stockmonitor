@echo off
chcp 65001 >nul 2>&1
set WATCH_FRAME_LOG=1
echo === debug run: frame log -> .frame_log.txt ===
echo === note the clock time when the flicker happens, then send the log ===
powershell -ExecutionPolicy Bypass -File "%~dp0watch_quotes.ps1" %*
if errorlevel 1 pause
