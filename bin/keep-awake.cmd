@echo off
setlocal

rem Windowsのスリープ抑制を開始するラッパー。
rem 対応するLinux版: なし。

set "SCRIPT=%~dp0..\scripts\keep-awake.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
exit /b %ERRORLEVEL%
