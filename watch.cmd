@echo off
chcp 65001 >nul 2>&1
setlocal
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell"
"%PS%" -NoProfile -NoLogo -ExecutionPolicy Bypass -File "%~dp0watch_quotes.ps1" %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
    if not "%WATCH_NO_PAUSE%"=="1" pause
)
exit /b %CODE%
