@echo off
setlocal

rem Windowsの電源設定を最適化するラッパー。
rem 対応するLinux版: なし。

set "SCRIPT=%~dp0..\libexec\optimize-power-settings.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
exit /b %ERRORLEVEL%
