@echo off
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0event-tracker\scripts\local-server.ps1" -Action start -Port 8630 -DatabasePath "data\tot-1125.db" %*
if errorlevel 1 exit /b 1
