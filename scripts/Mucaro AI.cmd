@echo off
rem Console fallback for the double-click setup (the normal way is to double-click "Mucaro AI.exe").
rem Shows setup progress in this window; closing the window stops Mucaro AI.
setlocal
title Mucaro AI
cd /d "%~dp0.."
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0one_click_windows.ps1" %*
set "MUCARO_RC=%ERRORLEVEL%"
rem Keep the window open after a failure so the message can be read (not in CI, where nobody can press a key).
if not "%MUCARO_RC%"=="0" if not defined CI pause
exit /b %MUCARO_RC%
