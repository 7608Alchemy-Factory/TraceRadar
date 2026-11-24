@echo off
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0event-tracker\scripts\local-server.ps1" -Action start -Port 8629 -DatabasePath "data\frontend-v2-preview.db" %*
if errorlevel 1 pause
