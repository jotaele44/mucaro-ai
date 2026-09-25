@echo off
rem Double-click this file (Windows) to set up and start Mucaro AI. No other steps are needed:
rem the first run installs private copies of Python and Node inside this folder, later runs
rem start in seconds. macOS: double-click "Mucaro AI.command" instead.
setlocal
title Mucaro AI
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\one_click_windows.ps1" %*
set "MUCARO_RC=%ERRORLEVEL%"
rem Keep the window open after a failure so the message can be read (not in CI, where nobody can press a key).
if not "%MUCARO_RC%"=="0" if not defined CI pause
exit /b %MUCARO_RC%
