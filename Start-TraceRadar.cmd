@echo off
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0event-tracker\scripts\local-server.ps1" -Action start
if errorlevel 1 pause
