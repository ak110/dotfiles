@echo off
setlocal
type "%USERPROFILE%\.ssh\conf.d\*.conf" > "%USERPROFILE%\.ssh\config" 2>nul
if exist "%USERPROFILE%\.ssh\localconfig" (
    type "%USERPROFILE%\.ssh\localconfig" >> "%USERPROFILE%\.ssh\config"
)
