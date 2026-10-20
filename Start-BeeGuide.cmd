@echo off
powershell -NoProfile -File "%~dp0run.ps1" -OpenBrowser
if errorlevel 1 pause
